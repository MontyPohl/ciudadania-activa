from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports_app", "0003_add_resolution_cycle_columns"),
        ("rewards_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="rewardtransaction",
            name="resolution_cycle",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
