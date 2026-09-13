from django.db import migrations, models

BONUS_EVENTS = ("VALIDATION_BONUS", "RESOLUTION_BONUS")


def ensure_reward_event_linkage_is_consistent(apps, schema_editor):
    RewardTransaction = apps.get_model("rewards_app", "RewardTransaction")
    invalid_bonus = RewardTransaction.objects.filter(event_type__in=BONUS_EVENTS).filter(
        models.Q(report_id__isnull=True)
        | models.Q(resolution_cycle__isnull=True)
        | models.Q(resolution_cycle__lt=1)
    )
    invalid_reversal = RewardTransaction.objects.filter(event_type="REVERSAL").filter(
        models.Q(report_id__isnull=True)
        | models.Q(resolution_cycle__isnull=True)
        | models.Q(resolution_cycle__lt=1)
        | models.Q(related_transaction_id__isnull=True)
    )
    invalid_related = RewardTransaction.objects.exclude(event_type="REVERSAL").filter(
        related_transaction_id__isnull=False
    )
    invalid = {
        "bonus": tuple(str(value) for value in invalid_bonus.values_list("id", flat=True)),
        "reversal": tuple(str(value) for value in invalid_reversal.values_list("id", flat=True)),
        "related": tuple(str(value) for value in invalid_related.values_list("id", flat=True)),
    }
    if any(invalid.values()):
        raise RuntimeError(
            "S10 reward linkage constraints aborted before schema changes: " f"invalid={invalid}"
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("rewards_app", "0003_enforce_resolution_cycle_constraints"),
    ]

    operations = [
        migrations.RunPython(
            ensure_reward_event_linkage_is_consistent,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(event_type__in=BONUS_EVENTS)
                    | (
                        models.Q(report__isnull=False)
                        & models.Q(resolution_cycle__isnull=False)
                        & models.Q(resolution_cycle__gte=1)
                    )
                ),
                name="reward_tx_bonus_cycle_required",
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(event_type="REVERSAL")
                    | (
                        models.Q(report__isnull=False)
                        & models.Q(resolution_cycle__isnull=False)
                        & models.Q(resolution_cycle__gte=1)
                        & models.Q(related_transaction__isnull=False)
                    )
                ),
                name="reward_tx_reversal_fields_required",
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(related_transaction__isnull=True) | models.Q(event_type="REVERSAL")
                ),
                name="reward_tx_related_only_reversal",
            ),
        ),
    ]
