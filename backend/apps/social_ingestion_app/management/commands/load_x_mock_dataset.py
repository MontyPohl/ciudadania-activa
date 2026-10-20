"""Carga por consola el dataset ficticio, sin conexiones a servicios externos."""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.social_ingestion_app.services.x_mock import DatasetError, ingest, parse_dataset


class Command(BaseCommand):
    help = "Carga publicaciones ficticias de X de forma atómica e idempotente."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Archivo JSON; ruta relativa a backend/.")
        parser.add_argument("--dry-run", action="store_true", help="Valida y cuenta sin escribir.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        try:
            with path.open(encoding="utf-8") as dataset_file:
                data = json.load(dataset_file)
        except (OSError, ValueError):
            raise CommandError("No se pudo leer el archivo JSON local.") from None
        try:
            items = parse_dataset(data)
        except DatasetError as exc:
            raise CommandError(str(exc)) from None
        result = ingest(items, dry_run=options["dry_run"])
        self.stdout.write(f"creados={result.created} omitidos={result.skipped} total={len(items)}")
