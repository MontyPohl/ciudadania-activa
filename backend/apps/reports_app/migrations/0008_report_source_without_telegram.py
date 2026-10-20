"""Sprint 13 (D-04, CT-43): se retira el valor `telegram` de `reports.source`.

El cambio de `choices` no emite SQL: no existe un CHECK de base sobre `source`. El
guard evita que queden reportes con un valor que el enumerado ya no reconoce.
"""

from django.db import migrations, models


def assert_no_telegram_reports(apps, schema_editor):
    """Aborta si algún reporte conserva `source = 'telegram'`."""
    alias = schema_editor.connection.alias
    Report = apps.get_model("reports_app", "Report")
    if Report.objects.using(alias).filter(source="telegram").exists():
        raise RuntimeError(
            "S13: migración abortada; existen reportes con source='telegram' "
            "y requieren revisión antes de retirar el valor (CT-43)."
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("reports_app", "0007_report_closed_at"),
    ]

    operations = [
        migrations.RunPython(assert_no_telegram_reports, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="report",
            name="source",
            field=models.CharField(
                choices=[("platform", "Plataforma"), ("x_mock", "X simulado")],
                default="platform",
                max_length=20,
            ),
        ),
    ]
