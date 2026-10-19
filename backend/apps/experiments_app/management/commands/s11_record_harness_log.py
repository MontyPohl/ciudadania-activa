from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.constants import ArtifactKind, Classification
from apps.experiments_app.runtime import evidence_store, valid_run_id, valid_scenario_id


class Command(BaseCommand):
    help = "Importa un log redactado del harness a la custodia inmutable S11."

    def add_arguments(self, parser):
        parser.add_argument("--input", type=Path, required=True)

    def handle(self, *args, **options):
        path = options["input"].resolve()
        control_root = (Path(settings.S11_ARTIFACT_ROOT).resolve() / "control").resolve()
        if control_root not in path.parents:
            raise CommandError("El log debe residir bajo la raíz de control S11.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError("El log del harness no es recuperable o JSON válido.") from exc
        run_id = payload.get("run_id", "")
        scenario_id = payload.get("scenario_id", "")
        if not valid_run_id(run_id) or not valid_scenario_id(scenario_id):
            raise CommandError("El log no contiene correlación S11 válida.")
        record = evidence_store().persist_json(
            run_id=run_id,
            scenario_id=scenario_id,
            artifact_type="harness-execution",
            kind=ArtifactKind.LOG,
            classification=Classification.INTERNAL,
            filename="harness-execution.json",
            payload=payload,
            source="s11-fullstack-harness",
        )
        try:
            path.unlink()
        except OSError as exc:
            raise CommandError(
                "Se importó el log, pero no pudo retirarse el control temporal."
            ) from exc
        self.stdout.write(self.style.SUCCESS(json.dumps(asdict(record), sort_keys=True)))
