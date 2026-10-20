"""Punto de entrada local S15; no modifica ajustes ni infraestructura."""

import json
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.final_dataset.generator import generate
from apps.core.final_dataset.isolation import DatasetError
from apps.core.final_dataset.recipe import Recipe


class Command(BaseCommand):
    help = "Genera o verifica por replay el dataset funcional S15 en un entorno local dedicado."

    def add_arguments(self, parser):
        parser.add_argument("--reference-date", required=True, type=date.fromisoformat)
        parser.add_argument("--seed", type=int, default=15)
        parser.add_argument("--reports", type=int, default=36)
        parser.add_argument("--artifact-root", required=True, type=Path)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--processes-stopped", action="store_true")

    def handle(self, *args, **options):
        try:
            recipe = Recipe(options["reference_date"], options["seed"], options["reports"])
            result = generate(
                recipe,
                options["artifact_root"],
                dry_run=options["dry_run"],
                processes_stopped=options["processes_stopped"],
            )
        except (DatasetError, ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
