"""Clasificador puro y fail-closed para el backfill histórico de S10.

La migración y el comando de preflight construyen estos mismos snapshots y llaman
``classify_universe``. El módulo no importa modelos Django para que la lógica pueda
usarse desde el registro histórico de migraciones sin depender del modelo vigente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

SEED_NAMESPACE = uuid.UUID("b63c21b4-6ac8-4ed3-b1ca-c6ca6862ab1d")
SEED_STATUS = {
    uuid.uuid5(SEED_NAMESPACE, f"s5-map-{index:02d}"): status
    for index, status in enumerate(
        (
            "CREADO",
            "CREADO",
            "CLASIFICADO",
            "CLASIFICADO",
            "ASIGNADO",
            "ASIGNADO",
            "EN_PROCESO",
            "EN_PROCESO",
            "RESUELTO",
            "RESUELTO",
            "CERRADO",
            "CERRADO",
            "RECHAZADO",
            "RECHAZADO",
        ),
        start=1,
    )
}

ADVANCED_STATUSES = {"ASIGNADO", "EN_PROCESO", "RESUELTO", "CERRADO"}
ALLOWED_HISTORY_TRANSITIONS = {
    ("", "CREADO"),
    ("CREADO", "CLASIFICADO"),
    ("CREADO", "ASIGNADO"),
    ("CLASIFICADO", "ASIGNADO"),
    ("CREADO", "RECHAZADO"),
    ("CLASIFICADO", "RECHAZADO"),
    ("ASIGNADO", "EN_PROCESO"),
    ("EN_PROCESO", "RESUELTO"),
    ("RESUELTO", "CERRADO"),
    ("ASIGNADO", "CLASIFICADO"),
    ("EN_PROCESO", "CLASIFICADO"),
    ("RESUELTO", "CLASIFICADO"),
    ("CERRADO", "CLASIFICADO"),
}


class Classification(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    CYCLE_0 = "CYCLE_0"
    CYCLE_1 = "CYCLE_1"
    UNKNOWN = "UNKNOWN"
    INCONSISTENT = "INCONSISTENT"


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    entity: str
    entity_id: str


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    id: object
    status: str
    citizen_id: object | None
    institution_id: object | None
    created_at: datetime
    validated_at: datetime | None
    validated_by_id: object | None
    resolved_at: datetime | None
    description: str
    resolution_cycle: int | None = None


@dataclass(frozen=True, slots=True)
class ConfirmationSnapshot:
    id: object
    report_id: object
    citizen_id: object
    created_at: datetime


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    id: object
    report_id: object
    previous_status: str
    new_status: str
    created_at: datetime
    resolution_cycle: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    id: object
    report_id: object
    created_at: datetime
    resolution_cycle: int | None = None


@dataclass(frozen=True, slots=True)
class RewardSnapshot:
    id: object
    event_type: str
    user_id: object
    report_id: object | None
    confirmation_id: object | None
    institution_id: object | None
    status: str
    impact_delta: int
    credit_delta: int
    created_at: datetime
    credited_at: datetime | None
    voided_at: datetime | None
    reversed_at: datetime | None
    related_transaction_id: object | None
    resolution_cycle: int | None = None


@dataclass(frozen=True, slots=True)
class AuditSnapshot:
    id: object
    action: str
    report_id: object | None
    institution_id: object | None
    cycle: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReportClassification:
    report_id: object
    classification: Classification
    reasons: tuple[Reason, ...]


@dataclass(frozen=True, slots=True)
class UniverseClassification:
    reports: tuple[ReportClassification, ...]
    global_reasons: tuple[Reason, ...]

    @property
    def invalid(self) -> bool:
        return bool(self.global_reasons) or any(
            item.classification in {Classification.UNKNOWN, Classification.INCONSISTENT}
            for item in self.reports
        )

    def ids_for(self, *classifications: Classification) -> tuple[object, ...]:
        accepted = set(classifications)
        return tuple(item.report_id for item in self.reports if item.classification in accepted)


def parse_uuid_reference(value):
    """Conserva referencias inválidas para que el clasificador las rechace."""
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return str(value)


def parse_cycle(value):
    if value is None:
        return None
    # JSON distingue enteros, flotantes, booleanos y cadenas. Solo un entero
    # real es una señal histórica admisible; convertir truncaría evidencia.
    return value if type(value) is int else -1


def classify_universe(
    *,
    reports: list[ReportSnapshot],
    confirmations: list[ConfirmationSnapshot],
    histories: list[HistorySnapshot],
    evidences: list[EvidenceSnapshot],
    rewards: list[RewardSnapshot],
    audits: list[AuditSnapshot],
) -> UniverseClassification:
    """Clasifica el universo completo sin efectuar escrituras ni usar fallbacks."""
    report_by_id = {item.id: item for item in reports}
    confirmation_by_id = {item.id: item for item in confirmations}
    reward_by_id = {item.id: item for item in rewards}
    reasons = {item.id: [] for item in reports}
    global_reasons: list[Reason] = []

    histories_by_report = {item.id: [] for item in reports}
    evidences_by_report = {item.id: [] for item in reports}
    rewards_by_report = {item.id: [] for item in reports}
    audits_by_report = {item.id: [] for item in reports}
    reward_owner_by_id = {}

    def reject(code, entity, entity_id, *candidate_report_ids):
        reason = Reason(code=code, entity=entity, entity_id=str(entity_id))
        attached = False
        for report_id in candidate_report_ids:
            if report_id in reasons:
                reasons[report_id].append(reason)
                attached = True
        if not attached:
            global_reasons.append(reason)

    for report in reports:
        if report.resolution_cycle is not None:
            reject("REPORT_CYCLE_PREPOPULATED", "report", report.id, report.id)
        if report.validated_at is not None and report.validated_at < report.created_at:
            reject("VALIDATION_BEFORE_REPORT", "report", report.id, report.id)
        if report.resolved_at is not None and report.resolved_at < report.created_at:
            reject("RESOLUTION_BEFORE_REPORT", "report", report.id, report.id)
        if (
            report.validated_at is not None
            and report.resolved_at is not None
            and report.resolved_at < report.validated_at
        ):
            reject("RESOLUTION_BEFORE_VALIDATION", "report", report.id, report.id)

    for confirmation in confirmations:
        report = report_by_id.get(confirmation.report_id)
        if report is None:
            reject("CONFIRMATION_REPORT_MISSING", "confirmation", confirmation.id)
        elif confirmation.created_at < report.created_at:
            reject(
                "CONFIRMATION_BEFORE_REPORT",
                "confirmation",
                confirmation.id,
                confirmation.report_id,
            )

    for history in histories:
        report = report_by_id.get(history.report_id)
        if report is None:
            reject("HISTORY_REPORT_MISSING", "history", history.id)
            continue
        histories_by_report[report.id].append(history)
        if history.resolution_cycle is not None:
            reject("HISTORY_CYCLE_PREPOPULATED", "history", history.id, report.id)
        if history.created_at < report.created_at:
            reject("HISTORY_BEFORE_REPORT", "history", history.id, report.id)

    for evidence in evidences:
        report = report_by_id.get(evidence.report_id)
        if report is None:
            reject("EVIDENCE_REPORT_MISSING", "evidence", evidence.id)
            continue
        evidences_by_report[report.id].append(evidence)
        if evidence.resolution_cycle is not None:
            reject("EVIDENCE_CYCLE_PREPOPULATED", "evidence", evidence.id, report.id)
        if evidence.created_at < report.created_at:
            reject("EVIDENCE_BEFORE_REPORT", "evidence", evidence.id, report.id)

    for reward in rewards:
        confirmation = (
            confirmation_by_id.get(reward.confirmation_id)
            if reward.confirmation_id is not None
            else None
        )
        confirmation_report_id = confirmation.report_id if confirmation is not None else None
        if not (
            reward.report_id is not None
            and confirmation_report_id is not None
            and reward.report_id != confirmation_report_id
        ):
            owner_id = _explicit_reward_owner(reward, confirmation_report_id)
            if owner_id is not None and not isinstance(owner_id, Reason):
                reward_owner_by_id[reward.id] = owner_id

    for reward in rewards:
        confirmation = (
            confirmation_by_id.get(reward.confirmation_id)
            if reward.confirmation_id is not None
            else None
        )
        confirmation_report_id = confirmation.report_id if confirmation is not None else None
        candidates = tuple(
            value for value in (reward.report_id, confirmation_report_id) if value is not None
        )

        if reward.confirmation_id is not None and confirmation is None:
            reject("REWARD_CONFIRMATION_MISSING", "reward", reward.id, reward.report_id)
            continue
        if (
            reward.report_id is not None
            and confirmation_report_id is not None
            and reward.report_id != confirmation_report_id
        ):
            reject("REWARD_OWNERSHIP_CONTRADICTORY", "reward", reward.id, *candidates)
            continue

        owner_id = _explicit_reward_owner(reward, confirmation_report_id)
        if owner_id is None:
            continue
        if isinstance(owner_id, Reason):
            reject(owner_id.code, owner_id.entity, owner_id.entity_id, *candidates)
            continue
        if owner_id not in report_by_id:
            reject("REWARD_REPORT_MISSING", "reward", reward.id, owner_id)
            continue

        report = report_by_id[owner_id]
        rewards_by_report[owner_id].append(reward)
        if reward.resolution_cycle is not None:
            reject("REWARD_CYCLE_PREPOPULATED", "reward", reward.id, owner_id)
        if reward.created_at < report.created_at:
            reject("REWARD_BEFORE_REPORT", "reward", reward.id, owner_id)
        if confirmation is not None and reward.created_at < confirmation.created_at:
            reject("REWARD_BEFORE_CONFIRMATION", "reward", reward.id, owner_id)
        for name, value in (
            ("CREDIT", reward.credited_at),
            ("VOID", reward.voided_at),
            ("REVERSAL", reward.reversed_at),
        ):
            if value is not None and value < report.created_at:
                reject(f"{name}_BEFORE_REPORT", "reward", reward.id, owner_id)
        if reward.voided_at is not None and reward.voided_at < reward.created_at:
            reject("VOID_BEFORE_REWARD", "reward", reward.id, owner_id)
        if (
            reward.event_type != "REVERSAL"
            and reward.reversed_at is not None
            and reward.reversed_at < reward.created_at
        ):
            reject("REVERSAL_BEFORE_REWARD", "reward", reward.id, owner_id)
        if reward.institution_id is not None and reward.institution_id != report.institution_id:
            reject("REWARD_INSTITUTION_MISMATCH", "reward", reward.id, owner_id)
        _validate_reward_shape(
            reward,
            report,
            confirmation,
            reward_by_id,
            reward_owner_by_id,
            reject,
            owner_id,
        )

    for audit_entry in audits:
        if audit_entry.report_id is None:
            if audit_entry.action.startswith("report."):
                reject("AUDIT_REPORT_MISSING", "audit", audit_entry.id)
            continue
        report = report_by_id.get(audit_entry.report_id)
        if report is None:
            reject("AUDIT_REPORT_NOT_FOUND", "audit", audit_entry.id)
            continue
        audits_by_report[report.id].append(audit_entry)
        if audit_entry.created_at < report.created_at:
            reject("AUDIT_BEFORE_REPORT", "audit", audit_entry.id, report.id)
        if (
            audit_entry.institution_id is not None
            and audit_entry.institution_id != report.institution_id
        ):
            reject("AUDIT_INSTITUTION_MISMATCH", "audit", audit_entry.id, report.id)
        if audit_entry.cycle is not None and audit_entry.cycle < 0:
            reject("AUDIT_CYCLE_NEGATIVE", "audit", audit_entry.id, report.id)

    classifications = []
    for report in sorted(reports, key=lambda item: str(item.id)):
        report_reasons = reasons[report.id]
        report_histories = sorted(
            histories_by_report[report.id], key=lambda item: (item.created_at, str(item.id))
        )
        report_evidences = evidences_by_report[report.id]
        report_rewards = rewards_by_report[report.id]
        report_audits = audits_by_report[report.id]

        if report.id in SEED_STATUS:
            classification = _classify_synthetic(
                report,
                report_histories,
                report_evidences,
                report_rewards,
                report_audits,
                report_reasons,
            )
        else:
            classification = _classify_real_report(
                report,
                report_histories,
                report_evidences,
                report_rewards,
                report_audits,
                report_reasons,
            )
        classifications.append(
            ReportClassification(report.id, classification, tuple(report_reasons))
        )

    return UniverseClassification(tuple(classifications), tuple(global_reasons))


def _explicit_reward_owner(reward, confirmation_report_id):
    if reward.event_type == "REPORT_CREATED":
        if reward.report_id is None or reward.confirmation_id is not None:
            return Reason("REPORT_CREATED_OWNERSHIP_INVALID", "reward", str(reward.id))
        return reward.report_id
    if reward.event_type == "CONFIRMATION_CREATED":
        if reward.confirmation_id is None or confirmation_report_id is None:
            return Reason("CONFIRMATION_CREATED_OWNERSHIP_INVALID", "reward", str(reward.id))
        return confirmation_report_id
    if reward.event_type in {"VALIDATION_BONUS", "RESOLUTION_BONUS", "REVERSAL"}:
        if reward.report_id is None or reward.confirmation_id is not None:
            return Reason("REWARD_DIRECT_OWNERSHIP_INVALID", "reward", str(reward.id))
        return reward.report_id
    if reward.event_type == "REDEMPTION":
        if reward.report_id is not None or reward.confirmation_id is not None:
            return Reason("REDEMPTION_REPORT_LINK_INVALID", "reward", str(reward.id))
        return None
    return Reason("REWARD_EVENT_UNKNOWN", "reward", str(reward.id))


def _validate_reward_shape(
    reward,
    report,
    confirmation,
    reward_by_id,
    reward_owner_by_id,
    reject,
    owner_id,
):
    timestamps_valid = {
        "PENDIENTE": (
            reward.credited_at is None and reward.voided_at is None and reward.reversed_at is None
        ),
        "ACREDITADO": (
            reward.credited_at is not None
            and reward.voided_at is None
            and reward.reversed_at is None
        ),
        "ANULADO": (
            reward.credited_at is None
            and reward.voided_at is not None
            and reward.reversed_at is None
        ),
        "REVERTIDO": reward.reversed_at is not None and reward.voided_at is None,
    }
    if not timestamps_valid.get(reward.status, False):
        reject("REWARD_STATUS_TIMESTAMPS_INVALID", "reward", reward.id, owner_id)

    expected_deltas = {
        "REPORT_CREATED": (20, 20),
        "CONFIRMATION_CREATED": (10, 10),
        "VALIDATION_BONUS": (30, 30),
        "RESOLUTION_BONUS": (50, 50),
    }
    if (
        reward.event_type in expected_deltas
        and (
            reward.impact_delta,
            reward.credit_delta,
        )
        != expected_deltas[reward.event_type]
    ):
        reject("REWARD_DELTAS_INVALID", "reward", reward.id, owner_id)

    if reward.event_type in {"REPORT_CREATED", "CONFIRMATION_CREATED"}:
        if reward.related_transaction_id is not None:
            reject("PARTICIPATION_RELATED_TRANSACTION_INVALID", "reward", reward.id, owner_id)
        if reward.status == "PENDIENTE" and reward.institution_id is not None:
            reject("PENDING_PARTICIPATION_HAS_INSTITUTION", "reward", reward.id, owner_id)
        if reward.status == "ACREDITADO" and (
            report.validated_at is None
            or reward.credited_at is None
            or reward.institution_id is None
        ):
            reject("ACCREDITED_WITHOUT_VALIDATION", "reward", reward.id, owner_id)
        if reward.event_type == "REPORT_CREATED" and reward.user_id != report.citizen_id:
            reject("REPORT_CREATED_USER_MISMATCH", "reward", reward.id, owner_id)
        if (
            reward.event_type == "CONFIRMATION_CREATED"
            and confirmation is not None
            and reward.user_id != confirmation.citizen_id
        ):
            reject("CONFIRMATION_CREATED_USER_MISMATCH", "reward", reward.id, owner_id)

    if reward.event_type in {"VALIDATION_BONUS", "RESOLUTION_BONUS"}:
        if (
            reward.status != "ACREDITADO"
            or reward.credited_at is None
            or reward.institution_id is None
            or reward.related_transaction_id is not None
        ):
            reject("BONUS_SHAPE_INVALID", "reward", reward.id, owner_id)
        if report.validated_at is None:
            reject("BONUS_WITHOUT_VALIDATION", "reward", reward.id, owner_id)

    if reward.event_type == "REVERSAL":
        original = reward_by_id.get(reward.related_transaction_id)
        if (
            reward.status != "REVERTIDO"
            or reward.related_transaction_id is None
            or original is None
        ):
            reject("REVERSAL_LINK_INVALID", "reward", reward.id, owner_id)
        elif original.event_type == "REVERSAL":
            reject("REVERSAL_OF_REVERSAL", "reward", reward.id, owner_id)
        else:
            if reward_owner_by_id.get(original.id) != owner_id:
                reject("REVERSAL_OWNERSHIP_MISMATCH", "reward", reward.id, owner_id)
            if original.status != "REVERTIDO" or original.reversed_at is None:
                reject("REVERSAL_ORIGINAL_NOT_REVERTED", "reward", reward.id, owner_id)
            if reward.created_at < original.created_at or (
                reward.reversed_at is not None
                and original.reversed_at is not None
                and reward.reversed_at < original.reversed_at
            ):
                reject("REVERSAL_TIMESTAMP_MISMATCH", "reward", reward.id, owner_id)
            if (
                reward.impact_delta != -original.impact_delta
                or reward.credit_delta != -original.credit_delta
                or reward.user_id != original.user_id
            ):
                reject("REVERSAL_ECONOMY_MISMATCH", "reward", reward.id, owner_id)


def _classify_synthetic(report, histories, evidences, rewards, audits, reasons):
    signature_matches = (
        report.citizen_id is None
        and report.status == SEED_STATUS[report.id]
        and report.validated_at is None
        and report.validated_by_id is None
        and report.resolved_at is None
        and not histories
        and not evidences
        and not rewards
        and not audits
        and report.description.startswith("Dato sintético S5-A ")
    )
    if not signature_matches:
        reasons.append(Reason("SYNTHETIC_SIGNATURE_MISMATCH", "report", str(report.id)))
    return Classification.INCONSISTENT if reasons else Classification.SYNTHETIC


def _classify_real_report(report, histories, evidences, rewards, audits, reasons):
    actions = {item.action for item in audits}
    transitions = {(item.previous_status, item.new_status) for item in histories}
    reward_events = {item.event_type for item in rewards}

    for index, history in enumerate(histories):
        if (history.previous_status, history.new_status) not in ALLOWED_HISTORY_TRANSITIONS:
            reasons.append(Reason("HISTORY_TRANSITION_INVALID", "history", str(history.id)))
        if index == 0:
            if history.previous_status != "" or history.new_status != "CREADO":
                reasons.append(Reason("HISTORY_INITIAL_STATE_INVALID", "history", str(history.id)))
        elif history.previous_status != histories[index - 1].new_status:
            reasons.append(Reason("HISTORY_CHAIN_BROKEN", "history", str(history.id)))
    if histories and histories[-1].new_status != report.status:
        reasons.append(Reason("HISTORY_FINAL_STATUS_MISMATCH", "report", str(report.id)))

    has_report_validation = report.validated_at is not None or report.validated_by_id is not None
    historical_validation = bool(
        transitions.intersection(
            {
                ("CREADO", "ASIGNADO"),
                ("CLASIFICADO", "ASIGNADO"),
            }
        )
        or "report.validated" in actions
        or reward_events.intersection({"VALIDATION_BONUS", "RESOLUTION_BONUS", "REVERSAL"})
        or evidences
    )

    if (report.validated_at is None) != (report.validated_by_id is None):
        reasons.append(Reason("VALIDATION_MARKERS_PARTIAL", "report", str(report.id)))
    if report.validated_at is None and historical_validation:
        reasons.append(Reason("VALIDATION_SIGNAL_CONTRADICTION", "report", str(report.id)))
    if report.validated_at is not None:
        if report.institution_id is None:
            reasons.append(Reason("VALIDATED_WITHOUT_INSTITUTION", "report", str(report.id)))
        validation_histories = [item for item in histories if item.new_status == "ASIGNADO"]
        validation_audits = [item for item in audits if item.action == "report.validated"]
        if not validation_histories:
            reasons.append(Reason("VALIDATION_HISTORY_MISSING", "report", str(report.id)))
        elif any(item.created_at < report.validated_at for item in validation_histories):
            reasons.append(Reason("VALIDATION_HISTORY_BEFORE_MARKER", "report", str(report.id)))
        if not validation_audits:
            reasons.append(Reason("VALIDATION_AUDIT_MISSING", "report", str(report.id)))
        elif any(item.created_at < report.validated_at for item in validation_audits):
            reasons.append(Reason("VALIDATION_AUDIT_BEFORE_MARKER", "report", str(report.id)))
        if any(item.created_at < report.validated_at for item in evidences):
            reasons.append(Reason("EVIDENCE_BEFORE_VALIDATION", "report", str(report.id)))
        if any(
            item.event_type in {"VALIDATION_BONUS", "RESOLUTION_BONUS"}
            and item.credited_at is not None
            and item.credited_at < report.validated_at
            for item in rewards
        ):
            reasons.append(Reason("BONUS_BEFORE_VALIDATION", "report", str(report.id)))
    if report.status in ADVANCED_STATUSES and not has_report_validation:
        reasons.append(Reason("ADVANCED_STATE_WITHOUT_VALIDATION", "report", str(report.id)))

    expected_audit_cycle = 1 if report.validated_at is not None else 0
    if any(audit.cycle is not None and audit.cycle != expected_audit_cycle for audit in audits):
        reasons.append(Reason("AUDIT_CYCLE_MISMATCH", "report", str(report.id)))

    resolution_signals = bool(
        report.resolved_at is not None
        or evidences
        or ("EN_PROCESO", "RESUELTO") in transitions
        or "report.resolved" in actions
        or "RESOLUTION_BONUS" in reward_events
    )
    if resolution_signals and report.validated_at is None:
        reasons.append(Reason("RESOLUTION_WITHOUT_VALIDATION", "report", str(report.id)))
    if report.status in {"RESUELTO", "CERRADO"} and report.resolved_at is None:
        reasons.append(Reason("RESOLVED_STATE_WITHOUT_TIMESTAMP", "report", str(report.id)))
    if report.resolved_at is not None:
        resolution_histories = [item for item in histories if item.new_status == "RESUELTO"]
        resolution_audits = [item for item in audits if item.action == "report.resolved"]
        if not resolution_histories:
            reasons.append(Reason("RESOLUTION_HISTORY_MISSING", "report", str(report.id)))
        elif any(item.created_at < report.resolved_at for item in resolution_histories):
            reasons.append(Reason("RESOLUTION_HISTORY_BEFORE_MARKER", "report", str(report.id)))
        if not resolution_audits:
            reasons.append(Reason("RESOLUTION_AUDIT_MISSING", "report", str(report.id)))
        elif any(item.created_at < report.resolved_at for item in resolution_audits):
            reasons.append(Reason("RESOLUTION_AUDIT_BEFORE_MARKER", "report", str(report.id)))
        if any(item.created_at > report.resolved_at for item in evidences):
            reasons.append(Reason("EVIDENCE_AFTER_RESOLUTION", "report", str(report.id)))
        if any(
            item.event_type == "RESOLUTION_BONUS"
            and item.credited_at is not None
            and item.credited_at < report.resolved_at
            for item in rewards
        ):
            reasons.append(Reason("RESOLUTION_BONUS_BEFORE_MARKER", "report", str(report.id)))

    if reasons:
        return Classification.INCONSISTENT
    if report.validated_at is not None:
        return Classification.CYCLE_1

    initial_history = [
        item for item in histories if item.previous_status == "" and item.new_status == "CREADO"
    ]
    allowed_cycle_zero_actions = {"report.created", "ai.report.classified", "report.rejected"}
    if any(item.action not in allowed_cycle_zero_actions for item in audits):
        return Classification.UNKNOWN
    if report.status == "CREADO" and len(histories) == 1 and len(initial_history) == 1:
        if all(item.status == "PENDIENTE" for item in rewards):
            return Classification.CYCLE_0
    return Classification.UNKNOWN
