from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports_app", "0006_resolution_evidence_idempotency"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(
                fields=["status", "resolved_at"],
                name="report_auto_close_idx",
            ),
        ),
    ]
