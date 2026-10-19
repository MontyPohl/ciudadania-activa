from __future__ import annotations

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from redis import Redis

from apps.admin_app.models import Category, Institution
from apps.experiments_app.isolation import (
    initialize_object_root,
    reset_object_root,
)
from apps.experiments_app.runtime import isolation_config, require_isolation
from apps.reports_app.models import Report


class Command(BaseCommand):
    help = "Crea, verifica o resetea exclusivamente el estado aislado S11."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["create", "verify", "reset"])

    def handle(self, *args, **options):
        action = options["action"]
        try:
            report = require_isolation()
            if action == "create":
                call_command("migrate", interactive=False, verbosity=0)
                call_command("seed_catalogs", verbosity=0)
                initialize_object_root(isolation_config())
            elif action == "reset":
                call_command("flush", interactive=False, verbosity=0)
                call_command("seed_catalogs", verbosity=0)
                reset_object_root(isolation_config())
                Redis.from_url(isolation_config().redis_url).flushdb()
                cache.clear()
            self._verify_clean()
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"S11_ENVIRONMENT_{action.upper()}_OK database={report.database_name} "
                f"redis_db={report.redis_db}"
            )
        )

    @staticmethod
    def _verify_clean() -> None:
        expected_categories = 10
        expected_institutions = 3
        facts = {
            "reports": Report.objects.count(),
            "categories": Category.objects.count(),
            "institutions": Institution.objects.count(),
        }
        if facts != {
            "reports": 0,
            "categories": expected_categories,
            "institutions": expected_institutions,
        }:
            raise CommandError(f"El estado aislado no está limpio: {facts}")
