from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.context import TraceContext, reset_trace, set_trace
from apps.experiments_app.runtime import (
    require_isolation,
    valid_request_id,
    valid_run_id,
    valid_scenario_id,
)
from apps.reports_app.tasks import auto_close_reports


class Command(BaseCommand):
    help = "Encola el autocierre real en Celery y espera su resultado sólo en S11 aislado."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=30)
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--scenario", required=True)
        parser.add_argument("--request-id", required=True)

    def handle(self, *args, **options):
        require_isolation()
        if not (
            valid_run_id(options["run_id"])
            and valid_scenario_id(options["scenario"])
            and valid_request_id(options["request_id"])
        ):
            raise CommandError("La correlación S11 para Celery no es válida.")
        token = set_trace(
            TraceContext(
                run_id=options["run_id"],
                scenario_id=options["scenario"],
                request_id=options["request_id"],
            )
        )
        try:
            result = auto_close_reports.delay()
        finally:
            reset_trace(token)
        try:
            closed = result.get(timeout=options["timeout"], disable_sync_subtasks=False)
        except Exception as exc:
            raise CommandError("La tarea real de autocierre no terminó correctamente.") from exc
        self.stdout.write(self.style.SUCCESS(f"EX05_AUTO_CLOSE_FINISHED closed={closed}"))
