import uuid

from django.db import migrations

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

EXPECTED_TOTAL = 16
EXPECTED_SYNTHETIC = 14
EXPECTED_CYCLE_0 = 2
EXPECTED_CYCLE_1 = 0


class BackfillAborted(RuntimeError):
    pass


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


def _load_snapshots(apps):
    Report = apps.get_model("reports_app", "Report")
    Confirmation = apps.get_model("reports_app", "ReportConfirmation")
    History = apps.get_model("reports_app", "ReportStatusHistory")
    Evidence = apps.get_model("reports_app", "ResolutionEvidence")
    Reward = apps.get_model("rewards_app", "RewardTransaction")
    AuditLog = apps.get_model("admin_app", "AuditLog")

    reports = [
        ReportSnapshot(
            id=item.id,
            status=item.status,
            citizen_id=item.citizen_id,
            institution_id=item.institution_id,
            created_at=item.created_at,
            validated_at=item.validated_at,
            validated_by_id=item.validated_by_id,
            resolved_at=item.resolved_at,
            description=item.description,
            resolution_cycle=item.resolution_cycle,
        )
        for item in Report.objects.order_by("id")
    ]
    confirmations = [
        ConfirmationSnapshot(
            id=item.id,
            report_id=item.report_id,
            citizen_id=item.citizen_id,
            created_at=item.created_at,
        )
        for item in Confirmation.objects.order_by("id")
    ]
    histories = [
        HistorySnapshot(
            id=item.id,
            report_id=item.report_id,
            previous_status=item.previous_status,
            new_status=item.new_status,
            created_at=item.created_at,
            resolution_cycle=item.resolution_cycle,
        )
        for item in History.objects.order_by("id")
    ]
    evidences = [
        EvidenceSnapshot(
            id=item.id,
            report_id=item.report_id,
            created_at=item.created_at,
            resolution_cycle=item.resolution_cycle,
        )
        for item in Evidence.objects.order_by("id")
    ]
    rewards = [
        RewardSnapshot(
            id=item.id,
            event_type=item.event_type,
            user_id=item.user_id,
            report_id=item.report_id,
            confirmation_id=item.confirmation_id,
            institution_id=item.institution_id,
            status=item.status,
            impact_delta=item.impact_delta,
            credit_delta=item.credit_delta,
            created_at=item.created_at,
            credited_at=item.credited_at,
            voided_at=item.voided_at,
            reversed_at=item.reversed_at,
            related_transaction_id=item.related_transaction_id,
            resolution_cycle=item.resolution_cycle,
        )
        for item in Reward.objects.order_by("id")
    ]
    audits = []
    for item in AuditLog.objects.order_by("id"):
        payload = item.payload if isinstance(item.payload, dict) else {}
        audits.append(
            AuditSnapshot(
                id=item.id,
                action=item.action,
                report_id=_uuid_or_none(payload.get("report_id")),
                institution_id=_uuid_or_none(payload.get("institution_id")),
                cycle=_integer_or_invalid(payload.get("cycle")),
                created_at=item.created_at,
            )
        )
    return reports, confirmations, histories, evidences, rewards, audits


def _ids(items):
    return frozenset(item.id for item in items)


def _assert_universe_unchanged(apps, snapshots):
    model_names = (
        ("reports_app", "Report"),
        ("reports_app", "ReportConfirmation"),
        ("reports_app", "ReportStatusHistory"),
        ("reports_app", "ResolutionEvidence"),
        ("rewards_app", "RewardTransaction"),
        ("admin_app", "AuditLog"),
    )
    for (app_label, model_name), expected in zip(model_names, snapshots, strict=True):
        model = apps.get_model(app_label, model_name)
        if frozenset(model.objects.values_list("id", flat=True)) != _ids(expected):
            raise BackfillAborted(
                f"S10 {model_name} universe changed after classification; aborting."
            )


def backfill_resolution_cycles(apps, schema_editor):
    snapshots = _load_snapshots(apps)
    reports, confirmations, histories, evidences, rewards, audits = snapshots
    if not reports:
        return

    result = classify_universe(
        reports=reports,
        confirmations=confirmations,
        histories=histories,
        evidences=evidences,
        rewards=rewards,
        audits=audits,
    )
    counts = {
        classification: len(result.ids_for(classification)) for classification in Classification
    }
    expected = {
        Classification.SYNTHETIC: EXPECTED_SYNTHETIC,
        Classification.CYCLE_0: EXPECTED_CYCLE_0,
        Classification.CYCLE_1: EXPECTED_CYCLE_1,
        Classification.UNKNOWN: 0,
        Classification.INCONSISTENT: 0,
    }
    if len(reports) != EXPECTED_TOTAL or counts != expected or result.global_reasons:
        reasons = [
            f"{reason.code}:{reason.entity}:{reason.entity_id}"
            for item in result.reports
            for reason in item.reasons
        ] + [
            f"{reason.code}:{reason.entity}:{reason.entity_id}" for reason in result.global_reasons
        ]
        count_text = ", ".join(
            f"{classification.value}:{count}" for classification, count in counts.items()
        )
        raise BackfillAborted(
            "S10 resolution-cycle backfill aborted before writes: "
            f"total={len(reports)} counts={{{count_text}}} reasons={sorted(reasons)}"
        )

    _assert_universe_unchanged(apps, snapshots)
    Report = apps.get_model("reports_app", "Report")
    History = apps.get_model("reports_app", "ReportStatusHistory")
    cycle_zero_ids = result.ids_for(Classification.SYNTHETIC, Classification.CYCLE_0)
    Report.objects.filter(pk__in=cycle_zero_ids).update(resolution_cycle=0)
    History.objects.filter(report_id__in=cycle_zero_ids).update(resolution_cycle=0)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("reports_app", "0003_add_resolution_cycle_columns"),
        ("rewards_app", "0002_add_resolution_cycle_column"),
    ]

    operations = [
        migrations.RunPython(backfill_resolution_cycles, migrations.RunPython.noop),
    ]
