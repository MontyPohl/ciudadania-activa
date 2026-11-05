# Posterior a S11. Solo crea una tabla nueva: no toca filas ni columnas existentes.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("admin_app", "0002_seed_catalogs"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("reports_app", "0008_report_source_without_telegram"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReportReassignment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reason", models.TextField()),
                (
                    "from_category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="admin_app.category",
                    ),
                ),
                (
                    "from_institution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="admin_app.institution",
                    ),
                ),
                (
                    "reassigned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reassignments",
                        to="reports_app.report",
                    ),
                ),
                (
                    "to_category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="admin_app.category",
                    ),
                ),
                (
                    "to_institution",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="admin_app.institution",
                    ),
                ),
            ],
            options={
                "db_table": "report_reassignments",
                "ordering": ["created_at"],
            },
        ),
    ]
