"""Sprint 13 (D-04, CT-43): Telegram queda fuera del MVP y se elimina su tabla.

La operación es destructiva (`DROP TABLE telegram_ingested_messages`). Por eso el
guard aborta antes de tocar el esquema si la tabla tiene filas: la eliminación solo
procede sobre una tabla vacía, verificada además en producción en solo lectura y con
`pg_dump` previo (ADR-0003).
"""

from django.db import migrations


def assert_no_telegram_rows(apps, schema_editor):
    """Aborta antes de borrar la tabla si contiene algún mensaje de Telegram."""
    alias = schema_editor.connection.alias
    # Modelo histórico: en este punto del grafo todavía existe.
    Message = apps.get_model("social_ingestion_app", "TelegramIngestedMessage")
    if Message.objects.using(alias).exists():
        raise RuntimeError(
            "S13: migración abortada antes de borrar la tabla; "
            "telegram_ingested_messages tiene filas y requiere revisión (CT-43)."
        )


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("social_ingestion_app", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(assert_no_telegram_rows, migrations.RunPython.noop),
        migrations.DeleteModel(
            name="TelegramIngestedMessage",
        ),
    ]
