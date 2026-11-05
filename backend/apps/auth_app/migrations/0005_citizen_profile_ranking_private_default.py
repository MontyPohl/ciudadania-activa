# Posterior a S11. Cambia solo el valor por defecto de los perfiles nuevos: Django lo aplica
# en Python, así que no hay SQL de datos y los perfiles existentes conservan su preferencia.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auth_app", "0004_citizen_profile_rejection_reason"),
    ]

    operations = [
        migrations.AlterField(
            model_name="citizenprofile",
            name="ranking_visibility",
            field=models.CharField(
                choices=[
                    ("FULL_NAME", "Nombre completo"),
                    ("FIRST_INITIAL", "Nombre e inicial"),
                    ("ALIAS", "Alias"),
                    ("PRIVATE", "Usuario privado"),
                ],
                default="PRIVATE",
                max_length=20,
            ),
        ),
    ]
