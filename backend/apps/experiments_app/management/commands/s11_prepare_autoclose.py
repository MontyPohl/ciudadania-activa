from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.enums import ReportStatus
from apps.experiments_app.runtime import require_isolation
from apps.reports_app.models import Report


class Command(BaseCommand):
    help = "Fija únicamente la precondición temporal sintética de EX-05 en el entorno aislado."

    def add_arguments(self, parser):
        parser.add_argument("report_id")

    def handle(self, *args, **options):
        require_isolation()
        try:
            report = Report.objects.get(pk=options["report_id"], status=ReportStatus.RESUELTO)
        except (Report.DoesNotExist, ValueError) as exc:
            raise CommandError("EX-05 requiere un reporte sintético RESUELTO recuperable.") from exc
        report.resolved_at = timezone.now() - timedelta(hours=settings.REPORT_AUTO_CLOSE_HOURS)
        report.save(update_fields=["resolved_at", "updated_at"])
        self.stdout.write(
            self.style.SUCCESS(f"EX05_CLOCK_PRECONDITION_READY report_id={report.pk}")
        )
