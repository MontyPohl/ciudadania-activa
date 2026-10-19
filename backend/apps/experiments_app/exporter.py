from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q

from apps.admin_app.models import AuditLog, Category, Institution
from apps.ai_client_app.models import AiClassification
from apps.auth_app.models import CitizenProfile
from apps.reports_app.models import (
    Report,
    ReportConfirmation,
    ReportMedia,
    ReportStatusHistory,
    ResolutionEvidence,
)
from apps.rewards_app.models import RewardTransaction

from .redaction import redact
from .runtime import require_isolation

_READ_ONLY = re.compile(r"^\s*(?:SELECT|WITH|SHOW|EXPLAIN|SET\s+TRANSACTION\s+READ\s+ONLY)\b", re.I)


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExportScope:
    run_id: str
    scenario_id: str
    report_ids: tuple[str, ...]
    user_ids: tuple[str, ...]


class ReadOnlySqlGuard:
    def __call__(self, execute, sql, params, many, context):
        if not _READ_ONLY.match(sql):
            raise ExportError(f"El exportador bloqueó SQL no read-only: {sql.split(None, 1)[0]}")
        return execute(sql, params, many, context)


def export_scope(scope: ExportScope) -> dict[str, Any]:
    """Export minimizado, estable y sin cálculos de indicadores."""
    require_isolation()
    report_ids = tuple(sorted(set(scope.report_ids)))
    user_ids = tuple(sorted(set(scope.user_ids)))
    with connection.execute_wrapper(ReadOnlySqlGuard()), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        reports = list(
            Report.objects.filter(pk__in=report_ids)
            .order_by("id")
            .values(
                "id",
                "citizen_id",
                "category_id",
                "institution_id",
                "description",
                "status",
                "priority",
                "source",
                "validated_at",
                "validated_by_id",
                "resolved_at",
                "closed_at",
                "resolution_cycle",
                "rejection_reason",
                "created_at",
                "updated_at",
            )
        )
        related_user_ids = set(user_ids)
        for report in reports:
            if report["citizen_id"]:
                related_user_ids.add(str(report["citizen_id"]))
            if report["validated_by_id"]:
                related_user_ids.add(str(report["validated_by_id"]))

        profiles = list(
            CitizenProfile.objects.filter(user_id__in=related_user_ids)
            .order_by("id")
            .values("id", "user_id", "document_hmac", "verified_at", "verified_by_id")
        )
        source = {
            "institutions": _values(
                Institution.objects.filter(
                    id__in={row["institution_id"] for row in reports if row["institution_id"]}
                ),
                "id",
                "slug",
                "name",
                "is_active",
            ),
            "categories": _values(
                Category.objects.filter(
                    id__in={row["category_id"] for row in reports if row["category_id"]}
                ),
                "id",
                "slug",
                "name",
                "default_institution_id",
                "default_priority",
                "is_active",
            ),
            "users": _values(
                get_user_model().objects.filter(id__in=related_user_ids),
                "id",
                "role",
                "verification_status",
                "is_active",
                "date_joined",
            ),
            "citizen_profiles": [
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "verified_at": row["verified_at"],
                    "verified_by_id": row["verified_by_id"],
                }
                for row in profiles
            ],
            "reports": reports,
            "report_media": _values(
                ReportMedia.objects.filter(report_id__in=report_ids),
                "id",
                "report_id",
                "file_hash",
                "media_type",
                "uploaded_by_id",
                "created_at",
            ),
            "status_history": _values(
                ReportStatusHistory.objects.filter(report_id__in=report_ids),
                "id",
                "report_id",
                "previous_status",
                "new_status",
                "changed_by_id",
                "reason",
                "resolution_cycle",
                "created_at",
            ),
            "confirmations": _values(
                ReportConfirmation.objects.filter(report_id__in=report_ids),
                "id",
                "report_id",
                "citizen_id",
                "created_at",
            ),
            "resolution_evidence": _values(
                ResolutionEvidence.objects.filter(report_id__in=report_ids),
                "id",
                "report_id",
                "description",
                "uploaded_by_id",
                "resolution_cycle",
                "idempotency_key",
                "content_hash",
                "created_at",
            ),
            "reward_transactions": _values(
                RewardTransaction.objects.filter(
                    Q(report_id__in=report_ids) | Q(confirmation__report_id__in=report_ids)
                ),
                "id",
                "user_id",
                "report_id",
                "confirmation_id",
                "institution_id",
                "event_type",
                "impact_delta",
                "credit_delta",
                "status",
                "related_transaction_id",
                "credited_at",
                "voided_at",
                "reversed_at",
                "resolution_cycle",
                "created_at",
            ),
            "ai_classifications": _values(
                AiClassification.objects.filter(report_id__in=report_ids),
                "id",
                "report_id",
                "suggested_category_id",
                "suggested_institution_id",
                "suggested_priority",
                "confidence",
                "inconsistency_flag",
                "justification",
                "model_name",
                "created_at",
            ),
            "audit_logs": _audit_rows(report_ids, related_user_ids),
        }
    return {
        "schema_version": "s11-read-only-export-v1",
        "run_id": scope.run_id,
        "scenario_id": scope.scenario_id,
        "classification": "EXPERIMENTAL",
        "source": redact(source),
        "derived": {
            "citizen_profile_hmac_checks": [
                {
                    "profile_id": row["id"],
                    "document_hmac_present": bool(row["document_hmac"]),
                    "document_hmac_length": len(row["document_hmac"]),
                }
                for row in profiles
            ]
        },
        "interpretation": None,
        "redactions": [
            "emails",
            "document_hmac_values",
            "object_keys_and_signed_urls",
            "secrets_and_tokens",
            "exact_coordinates",
        ],
    }


def _values(queryset, *fields: str) -> list[dict[str, Any]]:
    return list(queryset.order_by("id").values(*fields))


def _audit_rows(report_ids: tuple[str, ...], user_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # El entorno ya fue validado como aislado; se inspecciona el audit completo para
    # no perder eventos de IA/SYSTEM con actor nulo y luego se minimiza por report/user.
    for row in AuditLog.objects.order_by("id").values(
        "id", "actor_user_id", "actor_type", "action", "payload", "created_at"
    ):
        report_id = str(row["payload"].get("report_id", ""))
        actor_id = str(row["actor_user_id"] or "")
        if report_id in report_ids or (
            row["action"].startswith("identity.") and actor_id in user_ids
        ):
            rows.append(row)
    return rows
