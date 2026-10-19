from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.constants import ArtifactKind, Classification
from apps.experiments_app.exporter import ExportScope, export_scope
from apps.experiments_app.runtime import evidence_store


class Command(BaseCommand):
    help = "Exporta filas S11 mediante consultas read-only y persiste un JSON redactado."

    def add_arguments(self, parser):
        parser.add_argument("--scope", type=Path, required=True)

    def handle(self, *args, **options):
        path = options["scope"].resolve()
        control_root = (Path(settings.S11_ARTIFACT_ROOT).resolve() / "control").resolve()
        if control_root not in path.parents:
            raise CommandError("El scope debe residir bajo la raíz de control S11.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            scope = ExportScope(
                run_id=raw["run_id"],
                scenario_id=raw["scenario_id"],
                report_ids=tuple(raw.get("report_ids", [])),
                user_ids=tuple(raw.get("user_ids", [])),
            )
            payload = export_scope(scope)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise CommandError("El scope externo no es válido.") from exc
        record = evidence_store().persist_json(
            run_id=scope.run_id,
            scenario_id=scope.scenario_id,
            artifact_type="source-export",
            kind=ArtifactKind.RAW,
            classification=Classification.EXPERIMENTAL,
            filename="source-export.json",
            payload=payload,
            source="django-orm-read-only",
        )
        try:
            path.unlink()
        except OSError as exc:
            raise CommandError(
                "El export se custodió, pero el scope de control no pudo retirarse."
            ) from exc
        self.stdout.write(self.style.SUCCESS(json.dumps(asdict(record), sort_keys=True)))
