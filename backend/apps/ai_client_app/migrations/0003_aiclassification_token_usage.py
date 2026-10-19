from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai_client_app', '0002_aiclassification_justification_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='aiclassification',
            name='input_tokens',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='aiclassification',
            name='output_tokens',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='aiclassification',
            name='total_tokens',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
