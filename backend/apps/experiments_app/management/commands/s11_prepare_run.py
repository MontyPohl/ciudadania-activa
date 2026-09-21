from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.canonical import sha256_file
from apps.experiments_app.constants import ArtifactKind, Classification
from apps.experiments_app.datasets import dataset_registry
from apps.experiments_app.manifest import create_manifest
from apps.experiments_app.provenance import collect_provenance
from apps.experiments_app.runtime import evidence_store, require_isolation, valid_scenario_id


class Command(BaseCommand):
    help = "Prepara un run manifest S11 sin ejecutar el escenario."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", type=Path, required=True)
        parser.add_argument("--scenario", required=True)
        parser.add_argument("--experiment-id", required=True)
        parser.add_argument("--nonce", required=True)
        parser.add_argument("--seed", type=int, default=20260921)
        parser.add_argument("--git-sha", required=True)
        parser.add_argument("--git-clean", action="store_true", required=True)

    def handle(self, *args, **options):
        require_isolation()
        scenario = options["scenario"]
        if not valid_scenario_id(scenario):
            raise CommandError("scenario_id no pertenece a EX-02…EX-10.")
        dataset_path = options["dataset"].resolve()
        try:
            dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError("El dataset no es recuperable o JSON válido.") from exc
        dataset_id = dataset.get("dataset_id", "")
        registry = dataset_registry(options["seed"])
        dataset_digest = sha256_file(dataset_path)
        registered = registry.get(dataset_id)
        if dataset.get("synthetic") is not True:
            raise CommandError("El dataset debe declarar synthetic=true.")
        if registered is None or registered["sha256"] != dataset_digest:
            raise CommandError("El dataset no coincide con el registro determinista S11-B.")
        if scenario not in registered["scenarios"]:
            raise CommandError("El dataset no está registrado para el escenario solicitado.")
        manifest = create_manifest(
            repo_root=settings.REPO_ROOT,
            started_at=datetime.now(UTC),
            environment=settings.S11_ENVIRONMENT,
            timezone=settings.TIME_ZONE,
            seed=options["seed"],
            dataset_id=dataset_id,
            dataset_sha256=dataset_digest,
            scenario_id=scenario,
            experiment_id=options["experiment_id"],
            execution_nonce=options["nonce"],
            configuration={
                "worker_concurrency": 2,
                "ai_adapter": "s11-deterministic-fake-v1",
                "sensitive_persistence": settings.S11_ALLOW_SENSITIVE_PERSISTENCE,
                **collect_provenance(),
            },
            expected_git_sha=options["git_sha"],
            supplied_git_sha=options["git_sha"],
            supplied_git_clean=options["git_clean"],
        )
        if not manifest.git_clean:
            raise CommandError("El run no puede prepararse con el working tree sucio.")
        record = evidence_store().persist_json(
            run_id=manifest.run_id,
            scenario_id=scenario,
            artifact_type="run-manifest-initial",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.initial.json",
            payload=manifest.to_dict(),
            source="s11_prepare_run",
        )
        self.stdout.write(
            self.style.SUCCESS(
                json.dumps(
                    {
                        "run_id": manifest.run_id,
                        "manifest_sha256": record.sha256,
                        "artifact_id": record.artifact_id,
                        "status": manifest.status,
                    },
                    sort_keys=True,
                )
            )
        )
