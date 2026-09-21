from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports_app", "0005_enforce_resolution_cycles"),
        ("rewards_app", "0002_add_resolution_cycle_column"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rewardtransaction",
            name="resolution_cycle",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("resolution_cycle__isnull", True),
                    ("resolution_cycle__gte", 0),
                    _connector="OR",
                ),
                name="reward_tx_cycle_nonnegative_or_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.UniqueConstraint(
                condition=models.Q(("event_type", "VALIDATION_BONUS")),
                fields=("report", "resolution_cycle"),
                name="reward_tx_validation_bonus_cycle_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.UniqueConstraint(
                condition=models.Q(("event_type", "RESOLUTION_BONUS")),
                fields=("report", "resolution_cycle"),
                name="reward_tx_resolution_bonus_cycle_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.UniqueConstraint(
                condition=models.Q(("event_type", "REVERSAL")),
                fields=("related_transaction",),
                name="reward_tx_reversal_original_unique",
            ),
        ),
    ]
