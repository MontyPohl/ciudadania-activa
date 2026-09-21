from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.constants import Decision
from apps.experiments_app.traceability import reconcile


class Command(BaseCommand):
    help = "Reconcilia un export S11 sin cambiar estados ni calcular hipótesis."

    def add_arguments(self, parser):
        parser.add_argument("export", type=Path)
        parser.add_argument("--complete", action="store_true")

    def handle(self, *args, **options):
        try:
            payload = json.loads(options["export"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError("El export no es recuperable o válido.") from exc
        result = reconcile(payload, complete_input=options["complete"])
        self.stdout.write(json.dumps(asdict(result), sort_keys=True))
        if options["complete"] and result.status != Decision.PASS.value:
            raise CommandError(f"La trazabilidad terminó en {result.status}.")
