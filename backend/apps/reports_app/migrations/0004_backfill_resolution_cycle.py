import uuid

from django.db import migrations
from django.db.models import Q

SEED_NAMESPACE = uuid.UUID("b63c21b4-6ac8-4ed3-b1ca-c6ca6862ab1d")
SEED_CODES = tuple(f"s5-map-{index:02d}" for index in range(1, 15))
SEED_IDS = frozenset(uuid.uuid5(SEED_NAMESPACE, code) for code in SEED_CODES)
SEED_STATUS = {
    "s5-map-01": "CREADO",
    "s5-map-02": "CREADO",
    "s5-map-03": "CLASIFICADO",
    "s5-map-04": "CLASIFICADO",
    "s5-map-05": "ASIGNADO",
    "s5-map-06": "ASIGNADO",
    "s5-map-07": "EN_PROCESO",
    "s5-map-08": "EN_PROCESO",
    "s5-map-09": "RESUELTO",
    "s5-map-10": "RESUELTO",
    "s5-map-11": "CERRADO",
    "s5-map-12": "CERRADO",
    "s5-map-13": "RECHAZADO",
    "s5-map-14": "RECHAZADO",
}
SEED_EXPECTED_STATUS = {
    uuid.uuid5(SEED_NAMESPACE, code): status for code, status in SEED_STATUS.items()
}

EXPECTED_TOTAL = 16
EXPECTED_CLASS_A = 2
EXPECTED_CLASS_B = 14


class BackfillAborted(RuntimeError):
    pass


def _has_validation_markers(report, histories, evidences, rewards):
    if report.validated_at is not None or report.validated_by_id is not None:
        return True
    if report.resolved_at is not None or evidences:
        return True
    if any(history.new_status == "ASIGNADO" for history in histories):
        return True
    return any(
        reward.event_type in {"VALIDATION_BONUS", "RESOLUTION_BONUS", "REVERSAL"}
        for reward in rewards
    )


def _classify(report, histories, evidences, rewards):
    validation_markers = _has_validation_markers(report, histories, evidences, rewards)

    if report.pk in SEED_IDS:
        if (
            report.citizen_id is None
            and report.status == SEED_EXPECTED_STATUS[report.pk]
            and report.validated_at is None
            and report.validated_by_id is None
            and report.resolved_at is None
            and not histories
            and not evidences
            and not rewards
            and report.description.startswith("Dato sintético S5-A ")
        ):
            return "B"
        return "INCONSISTENT"

    initial_histories = [
        history
        for history in histories
        if history.previous_status == "" and history.new_status == "CREADO"
    ]
    if (
        report.status == "CREADO"
        and len(histories) == 1
        and len(initial_histories) == 1
        and not validation_markers
    ):
        return "A"

    if validation_markers:
        return "INCONSISTENT"
    return "UNKNOWN"


def backfill_resolution_cycles(apps, schema_editor):
    Report = apps.get_model("reports_app", "Report")
    ReportStatusHistory = apps.get_model("reports_app", "ReportStatusHistory")
    ResolutionEvidence = apps.get_model("reports_app", "ResolutionEvidence")
    RewardTransaction = apps.get_model("rewards_app", "RewardTransaction")

    reports = list(Report.objects.order_by("id"))
    if not reports:
        return

    classifications = {}
    histories_by_report = {}
    evidences_by_report = {}
    rewards_by_report = {}

    for report in reports:
        histories = list(ReportStatusHistory.objects.filter(report_id=report.pk).order_by("id"))
        evidences = list(ResolutionEvidence.objects.filter(report_id=report.pk).order_by("id"))
        rewards = list(
            RewardTransaction.objects.filter(
                Q(report_id=report.pk) | Q(confirmation__report_id=report.pk)
            ).order_by("created_at", "id")
        )
        histories_by_report[report.pk] = histories
        evidences_by_report[report.pk] = evidences
        rewards_by_report[report.pk] = rewards
        classifications[report.pk] = _classify(report, histories, evidences, rewards)

    grouped = {
        label: sorted(
            str(report_id)
            for report_id, value in classifications.items()
            if value == label
        )
        for label in ("A", "B", "UNKNOWN", "INCONSISTENT")
    }
    if (
        len(reports) != EXPECTED_TOTAL
        or len(grouped["A"]) != EXPECTED_CLASS_A
        or len(grouped["B"]) != EXPECTED_CLASS_B
        or grouped["UNKNOWN"]
        or grouped["INCONSISTENT"]
    ):
        raise BackfillAborted(
            "S10 resolution-cycle backfill aborted before writes: "
            f"total={len(reports)} class_a={len(grouped['A'])} class_b={len(grouped['B'])} "
            f"unknown={grouped['UNKNOWN']} inconsistent={grouped['INCONSISTENT']}"
        )

    classified_ids = frozenset(classifications)
    current_ids = frozenset(Report.objects.values_list("id", flat=True))
    if current_ids != classified_ids:
        raise BackfillAborted("S10 report universe changed after classification; aborting.")

    Report.objects.filter(pk__in=classified_ids).update(resolution_cycle=0)
    ReportStatusHistory.objects.filter(report_id__in=classified_ids).update(resolution_cycle=0)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("reports_app", "0003_add_resolution_cycle_columns"),
        ("rewards_app", "0002_add_resolution_cycle_column"),
    ]

    operations = [
        migrations.RunPython(backfill_resolution_cycles, migrations.RunPython.noop),
    ]
