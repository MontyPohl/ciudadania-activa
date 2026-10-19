import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications_app", "0001_initial"),
        ("rewards_app", "0005_s12_redemption_hardening"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="redemption",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="notifications",
                to="rewards_app.rewardredemption",
            ),
        ),
    ]
