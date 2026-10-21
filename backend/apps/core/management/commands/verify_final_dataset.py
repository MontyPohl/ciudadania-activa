"""Lectura SQL/bytes independiente; salida en stdout, nunca repara datos."""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.final_dataset.files import read_manifest
from apps.core.final_dataset.isolation import DatasetError, guard_environment
from apps.core.final_dataset.verification import verify


class Command(BaseCommand):
    help = "Comprueba el dataset S15 por SQL directo, oráculo declarativo y hashes de bytes."

    def add_arguments(self, parser):
        parser.add_argument("manifest", type=Path)
        parser.add_argument("--artifact-root", required=True, type=Path)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            root = guard_environment(settings, options["artifact_root"])
            manifest_path = options["manifest"].resolve()
            if root not in manifest_path.parents:
                raise DatasetError("Manifiesto fuera del directorio de artefactos permitido.")
            if options["dry_run"]:
                manifest = read_manifest(manifest_path)
                result = {
                    "state": "dry-run",
                    "plan_sha256": manifest["plan_sha256"],
                    "effects": [],
                    "not_verified": ["SQL", "media bytes", "HTTP"],
                }
            else:
                result = verify(manifest_path, artifact_root=root)
        except (DatasetError, KeyError, ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if result.get("status") == "FAIL":
            raise CommandError(
                "Verificación S15 divergente; consulte las comprobaciones en stdout."
            )
