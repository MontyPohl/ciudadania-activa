"""Preflight de solo lectura para la ventana de mantenimiento del backfill S10."""

import hashlib
import json
import uuid
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError

from apps.admin_app.models import AuditLog
from apps.reports_app.models import (
    Report,
    ReportConfirmation,
    ReportStatusHistory,
    ResolutionEvidence,
)
from apps.reports_app.services.resolution_cycle_backfill import (
    AuditSnapshot,
    Classification,
    ConfirmationSnapshot,
    EvidenceSnapshot,
    HistorySnapshot,
    ReportSnapshot,
    RewardSnapshot,
    classify_universe,
)
from apps.rewards_app.models import RewardTransaction


class Command(BaseCommand):
    help = "Audita sin escribir el universo autorizado para el backfill S10."

    def add_arguments(self, parser):
        parser.add_argument("--web-writers-off", action="store_true")
        parser.add_argument("--worker-writers-off", action="store_true")
        parser.add_argument("--scheduled-writers-off", action="store_true")

    def handle(self, *args, **options):
        confirmations = (
            options["web_writers_off"],
            options["worker_writers_off"],
            options["scheduled_writers_off"],
        )
        if not all(confirmations):
            raise CommandError(
                "ABORT: confirmar web writers, worker writers y scheduled writers OFF."
            )

        snapshots = _load_readonly_snapshots()
        result = classify_universe(**snapshots)
        classes = {
            classification.value: [
                str(item.report_id)
                for item in result.reports
                if item.classification == classification
            ]
            for classification in Classification
        }
        reasons = [
            {
                "report_id": str(item.report_id),
                "code": reason.code,
                "entity": reason.entity,
                "entity_id": reason.entity_id,
            }
            for item in result.reports
            for reason in item.reasons
        ] + [
            {
                "report_id": None,
                "code": reason.code,
                "entity": reason.entity,
                "entity_id": reason.entity_id,
            }
            for reason in result.global_reasons
        ]
        fingerprint_rows = {
            key: [_serializable(item) for item in value] for key, value in snapshots.items()
        }
        payload = {
            "total_reports": len(snapshots["reports"]),
            "synthetic": len(classes[Classification.SYNTHETIC.value]),
            "cycle_0": len(classes[Classification.CYCLE_0.value]),
            "cycle_1": len(classes[Classification.CYCLE_1.value]),
            "unknown": len(classes[Classification.UNKNOWN.value]),
            "inconsistent": len(classes[Classification.INCONSISTENT.value]),
            "report_ids": classes,
            "reasons": reasons,
            "fingerprint_sha256": hashlib.sha256(
                json.dumps(fingerprint_rows, sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))

        actual = (
            payload["total_reports"],
            payload["synthetic"],
            payload["cycle_0"],
            payload["cycle_1"],
            payload["unknown"],
            payload["inconsistent"],
            len(result.global_reasons),
        )
        expected = (16, 14, 2, 0, 0, 0, 0)
        if actual != expected:
            raise CommandError(
                f"ABORT: universo no autorizado; esperado={expected}, actual={actual}"
            )
        self.stdout.write(self.style.SUCCESS("READONLY_AUDIT_COMPLETE"))


def _load_readonly_snapshots():
    reports = [
        ReportSnapshot(**row, resolution_cycle=None)
        for row in Report.objects.order_by("id").values(
            "id",
            "status",
            "citizen_id",
            "institution_id",
            "created_at",
            "validated_at",
            "validated_by_id",
            "resolved_at",
            "description",
        )
    ]
    confirmations = [
        ConfirmationSnapshot(**row)
        for row in ReportConfirmation.objects.order_by("id").values(
            "id", "report_id", "citizen_id", "created_at"
        )
    ]
    histories = [
        HistorySnapshot(**row, resolution_cycle=None)
        for row in ReportStatusHistory.objects.order_by("id").values(
            "id", "report_id", "previous_status", "new_status", "created_at"
        )
    ]
    evidences = [
        EvidenceSnapshot(**row, resolution_cycle=None)
        for row in ResolutionEvidence.objects.order_by("id").values("id", "report_id", "created_at")
    ]
    rewards = [
        RewardSnapshot(**row, resolution_cycle=None)
        for row in RewardTransaction.objects.order_by("id").values(
            "id",
            "event_type",
            "user_id",
            "report_id",
            "confirmation_id",
            "institution_id",
            "status",
            "impact_delta",
            "credit_delta",
            "created_at",
            "credited_at",
            "voided_at",
            "reversed_at",
            "related_transaction_id",
        )
    ]
    audits = []
    for row in AuditLog.objects.order_by("id").values("id", "action", "payload", "created_at"):
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        audits.append(
            AuditSnapshot(
                id=row["id"],
                action=row["action"],
                report_id=_uuid_or_none(payload.get("report_id")),
                institution_id=_uuid_or_none(payload.get("institution_id")),
                cycle=_integer_or_invalid(payload.get("cycle")),
                created_at=row["created_at"],
            )
        )
    return {
        "reports": reports,
        "confirmations": confirmations,
        "histories": histories,
        "evidences": evidences,
        "rewards": rewards,
        "audits": audits,
    }


def _uuid_or_none(value):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _integer_or_invalid(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _serializable(item):
    return {
        field: str(value) if value is not None else None for field, value in asdict(item).items()
    }
