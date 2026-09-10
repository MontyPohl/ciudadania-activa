"""Preflight de solo lectura para la ventana de mantenimiento del backfill S10."""

import hashlib
import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

SEED_NAMESPACE = uuid.UUID("b63c21b4-6ac8-4ed3-b1ca-c6ca6862ab1d")
SEED_IDS = frozenset(
    uuid.uuid5(SEED_NAMESPACE, f"s5-map-{index:02d}") for index in range(1, 15)
)
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

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, citizen_id, validated_at, validated_by_id,
                       resolved_at, description
                FROM reports
                ORDER BY id
                """
            )
            reports = cursor.fetchall()
            cursor.execute(
                """
                SELECT report_id, previous_status, new_status
                FROM report_status_history
                ORDER BY report_id, created_at, id
                """
            )
            histories = cursor.fetchall()
            cursor.execute(
                "SELECT report_id FROM resolution_evidences ORDER BY report_id, created_at, id"
            )
            evidences = cursor.fetchall()
            cursor.execute(
                """
                SELECT COALESCE(rt.report_id, rc.report_id), rt.event_type
                FROM reward_transactions rt
                LEFT JOIN report_confirmations rc ON rc.id = rt.confirmation_id
                WHERE COALESCE(rt.report_id, rc.report_id) IS NOT NULL
                ORDER BY COALESCE(rt.report_id, rc.report_id), rt.created_at, rt.id
                """
            )
            rewards = cursor.fetchall()

        history_map = _group_rows(histories)
        evidence_map = _group_rows(evidences)
        reward_map = _group_rows(rewards)
        classes = {label: [] for label in ("A", "B", "UNKNOWN", "INCONSISTENT")}
        fingerprint_rows = []
        for row in reports:
            report_id = row[0]
            label = _classify(
                row,
                history_map.get(report_id, []),
                evidence_map.get(report_id, []),
                reward_map.get(report_id, []),
            )
            classes[label].append(str(report_id))
            fingerprint_rows.append(
                [str(value) if value is not None else None for value in row]
                + [history_map.get(report_id, [])]
                + [evidence_map.get(report_id, [])]
                + [reward_map.get(report_id, [])]
            )

        payload = {
            "total_reports": len(reports),
            "class_a": len(classes["A"]),
            "class_b": len(classes["B"]),
            "unknown": len(classes["UNKNOWN"]),
            "inconsistent": len(classes["INCONSISTENT"]),
            "report_ids": classes,
            "fingerprint_sha256": hashlib.sha256(
                json.dumps(fingerprint_rows, sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
        self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))

        expected = (16, 2, 14, 0, 0)
        actual = (
            payload["total_reports"],
            payload["class_a"],
            payload["class_b"],
            payload["unknown"],
            payload["inconsistent"],
        )
        if actual != expected:
            raise CommandError(
                f"ABORT: universo no autorizado; esperado={expected}, actual={actual}"
            )
        self.stdout.write(self.style.SUCCESS("READONLY_AUDIT_COMPLETE"))


def _group_rows(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(
            tuple(str(value) if value is not None else None for value in row[1:])
        )
    return grouped


def _classify(report, histories, evidences, rewards):
    report_id, status, citizen_id, validated_at, validated_by_id, resolved_at, description = report
    validation_markers = bool(validated_at or validated_by_id or resolved_at or evidences)
    validation_markers = validation_markers or any(
        row and row[0] == "ASIGNADO" for row in histories
    )
    validation_markers = validation_markers or any(
        row and row[0] in {"VALIDATION_BONUS", "RESOLUTION_BONUS", "REVERSAL"}
        for row in rewards
    )
    if report_id in SEED_IDS:
        if (
            citizen_id is None
            and status == SEED_STATUS[report_id]
            and validated_at is None
            and validated_by_id is None
            and resolved_at is None
            and not histories
            and not evidences
            and not rewards
            and description.startswith("Dato sintético S5-A ")
        ):
            return "B"
        return "INCONSISTENT"
    if (
        status == "CREADO"
        and histories == [("", "CREADO")]
        and not validation_markers
    ):
        return "A"
    return "INCONSISTENT" if validation_markers else "UNKNOWN"
