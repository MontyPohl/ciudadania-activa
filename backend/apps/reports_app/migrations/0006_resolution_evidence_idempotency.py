from django.db import migrations, models


def ensure_no_historical_evidence(apps, schema_editor):
    ResolutionEvidence = apps.get_model("reports_app", "ResolutionEvidence")
    if ResolutionEvidence.objects.exists():
        raise RuntimeError(
            "S10 evidence migration aborted: the approved baseline has no historical evidence."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("reports_app", "0005_enforce_resolution_cycles"),
    ]

    operations = [
        migrations.RunPython(ensure_no_historical_evidence, migrations.RunPython.noop),
        migrations.AddField(
            model_name="resolutionevidence",
            name="idempotency_key",
            field=models.UUIDField(),
        ),
        migrations.AddField(
            model_name="resolutionevidence",
            name="content_hash",
            field=models.CharField(max_length=64),
        ),
        migrations.AddConstraint(
            model_name="resolutionevidence",
            constraint=models.UniqueConstraint(
                fields=("report", "resolution_cycle", "idempotency_key"),
                name="resolution_evidence_idempotency_unique",
            ),
        ),
    ]
