from django.db import migrations, models

CREATE_TRIGGER = """
CREATE OR REPLACE FUNCTION reports_resolution_cycle_monotonic()
RETURNS trigger AS $$
BEGIN
    IF NEW.resolution_cycle < OLD.resolution_cycle THEN
        RAISE EXCEPTION 'reports.resolution_cycle cannot decrease';
    END IF;
    IF NEW.resolution_cycle > OLD.resolution_cycle THEN
        IF NEW.resolution_cycle <> OLD.resolution_cycle + 1
           OR OLD.validated_at IS NOT NULL
           OR NEW.validated_at IS NULL
           OR NEW.status <> 'ASIGNADO' THEN
            RAISE EXCEPTION 'reports.resolution_cycle can advance only by one during validation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER reports_resolution_cycle_monotonic_trigger
BEFORE UPDATE OF resolution_cycle ON reports
FOR EACH ROW EXECUTE FUNCTION reports_resolution_cycle_monotonic();
"""

DROP_TRIGGER = """
DROP TRIGGER IF EXISTS reports_resolution_cycle_monotonic_trigger ON reports;
DROP FUNCTION IF EXISTS reports_resolution_cycle_monotonic();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("reports_app", "0004_backfill_resolution_cycle"),
    ]

    operations = [
        migrations.AlterField(
            model_name="report",
            name="resolution_cycle",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="reportstatushistory",
            name="resolution_cycle",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="resolutionevidence",
            name="resolution_cycle",
            field=models.PositiveIntegerField(),
        ),
        migrations.AddConstraint(
            model_name="report",
            constraint=models.CheckConstraint(
                condition=models.Q(("resolution_cycle__gte", 0)),
                name="report_resolution_cycle_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="reportstatushistory",
            constraint=models.CheckConstraint(
                condition=models.Q(("resolution_cycle__gte", 0)),
                name="report_history_cycle_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="resolutionevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(("resolution_cycle__gte", 1)),
                name="resolution_evidence_cycle_positive",
            ),
        ),
        migrations.RunSQL(CREATE_TRIGGER, reverse_sql=DROP_TRIGGER),
    ]
