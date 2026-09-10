from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports_app", "0002_reportmedia_report_media_foto_hash_unico"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="resolution_cycle",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reportstatushistory",
            name="resolution_cycle",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="resolutionevidence",
            name="resolution_cycle",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
