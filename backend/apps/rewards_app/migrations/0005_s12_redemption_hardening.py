import django.db.models.deletion
from django.db import migrations, models


def assert_preconditions(apps, schema_editor):
    """Aborta antes de alterar el esquema si los datos requieren un backfill."""
    alias = schema_editor.connection.alias
    Reward = apps.get_model("rewards_app", "Reward")
    RewardRedemption = apps.get_model("rewards_app", "RewardRedemption")
    RewardTransaction = apps.get_model("rewards_app", "RewardTransaction")
    invalid = {
        "redemptions": RewardRedemption.objects.using(alias).exists(),
        "redemption_tx": RewardTransaction.objects.using(alias)
        .filter(event_type="REDEMPTION")
        .exists(),
        "dup_rewards": Reward.objects.using(alias)
        .order_by()
        .values("institution_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
        .exists(),
    }
    if any(invalid.values()):
        raise RuntimeError(
            "S12: migración abortada antes de alterar el esquema; "
            f"se requiere revisar los datos: {invalid}"
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("admin_app", "0002_seed_catalogs"),
        ("rewards_app", "0004_enforce_reward_event_linkage"),
    ]

    operations = [
        migrations.RunPython(assert_preconditions, migrations.RunPython.noop),
        migrations.AddField(
            model_name="rewardredemption",
            name="institution",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="redemptions",
                to="admin_app.institution",
            ),
        ),
        migrations.AddField(
            model_name="rewardredemption",
            name="period_month",
            field=models.DateField(null=True),
        ),
        migrations.AlterField(
            model_name="rewardredemption",
            name="institution",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="redemptions",
                to="admin_app.institution",
            ),
        ),
        migrations.AlterField(
            model_name="rewardredemption",
            name="period_month",
            field=models.DateField(),
        ),
        migrations.AddField(
            model_name="rewardtransaction",
            name="redemption",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ledger_transaction",
                to="rewards_app.rewardredemption",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="reward",
            name="reward_unica_por_institucion",
        ),
        migrations.AddConstraint(
            model_name="reward",
            constraint=models.UniqueConstraint(
                fields=["institution"], name="reward_una_por_institucion"
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardredemption",
            constraint=models.UniqueConstraint(
                fields=["user", "institution", "period_month"],
                name="redemption_una_por_mes_institucion",
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardredemption",
            constraint=models.CheckConstraint(
                condition=models.Q(period_month__day=1), name="redemption_period_dia_uno"
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(redemption__isnull=True) | models.Q(event_type="REDEMPTION"),
                name="reward_tx_redemption_only_redemption",
            ),
        ),
        migrations.AddConstraint(
            model_name="rewardtransaction",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(event_type="REDEMPTION")
                    | (
                        models.Q(redemption__isnull=False)
                        & models.Q(impact_delta=0)
                        & models.Q(credit_delta__lt=0)
                        & models.Q(institution__isnull=False)
                        & models.Q(report__isnull=True)
                        & models.Q(confirmation__isnull=True)
                        & models.Q(status="ACREDITADO")
                    )
                ),
                name="reward_tx_redemption_shape",
            ),
        ),
    ]
