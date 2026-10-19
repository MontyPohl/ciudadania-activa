from __future__ import annotations

import json
from datetime import UTC, datetime

from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.constants import ArtifactKind, Classification, RunStatus
from apps.experiments_app.manifest import (
    ArtifactReference,
    RunManifest,
    finalize_manifest,
)
from apps.experiments_app.runtime import evidence_store, valid_run_id


class Command(BaseCommand):
    help = "Cierra un run S11 con manifest final y referencias SHA-256 verificadas."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument(
            "--status",
            required=True,
            choices=[
                RunStatus.COMPLETED.value,
                RunStatus.FAILED.value,
                RunStatus.INVALID.value,
            ],
        )
        parser.add_argument("--invalidity-reason")

    def handle(self, *args, **options):
        run_id = options["run_id"]
        if not valid_run_id(run_id):
            raise CommandError("run_id no tiene el formato S11 esperado.")

        store = evidence_store()
        with store.mutation():
            result = self._finalize_locked(store, run_id, options)
        self.stdout.write(self.style.SUCCESS(json.dumps(result, sort_keys=True)))

    def _finalize_locked(self, store, run_id, options):
        store.recover_finalization(run_id)
        records = store.records(run_id)
        initial = [
            record for record in records if record["artifact_type"] == "run-manifest-initial"
        ]
        if len(initial) != 1:
            raise CommandError("El run debe tener exactamente un manifest inicial.")
        if any(record["artifact_type"] == "run-manifest-final" for record in records):
            raise CommandError("El run ya tiene un manifest final inmutable.")

        scenario_id = initial[0]["scenario_id"]
        indexed_types = {record["artifact_type"] for record in records}
        for stream in ("http", "celery"):
            artifact_type = f"{stream}-events"
            path = (
                store.initialize_run(run_id)
                / ArtifactKind.LOG.value
                / scenario_id
                / f"{stream}.jsonl"
            )
            if path.is_file() and artifact_type not in indexed_types:
                store.register_existing(
                    run_id=run_id,
                    scenario_id=scenario_id,
                    artifact_type=artifact_type,
                    kind=ArtifactKind.LOG,
                    classification=Classification.INTERNAL,
                    filename=path.name,
                    source="s11-instrumentation",
                    transformation="automatic-redaction+append-only-jsonl",
                )
        records = store.records(run_id)
        verification_errors = store.verify(run_id)
        if verification_errors:
            raise CommandError(f"Falló la verificación previa: {verification_errors}")

        run_root = store.initialize_run(run_id)
        initial_payload = json.loads(
            (run_root / initial[0]["relative_path"]).read_text(encoding="utf-8")
        )
        initial_payload["artifact_references"] = tuple(
            ArtifactReference(**reference)
            for reference in initial_payload.get("artifact_references", [])
        )
        manifest = RunManifest(**initial_payload)

        references = [
            ArtifactReference(
                artifact_id=record["artifact_id"],
                relative_path=record["relative_path"],
                sha256=record["sha256"],
            )
            for record in records
        ]

        status = RunStatus(options["status"])
        finished_at = datetime.now(UTC)
        try:
            final = finalize_manifest(
                manifest,
                finished_at=finished_at,
                status=status,
                artifacts=references,
                invalidity_reason=options["invalidity_reason"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        record = store.finalize_with_json_artifact(
            finished_at=finished_at,
            run_id=run_id,
            scenario_id=manifest.scenario_id,
            artifact_type="run-manifest-final",
            kind=ArtifactKind.METADATA,
            classification=Classification.INTERNAL,
            filename="run-manifest.final.json",
            payload=final.to_dict(),
            source="s11_finalize_run",
        )
        final_errors = store.verify(run_id)
        if final_errors:
            raise CommandError(f"Falló la verificación final: {final_errors}")
        return {
            "artifact_count": len(references),
            "manifest_sha256": record.sha256,
            "run_id": run_id,
            "status": final.status,
            "verification": "PASS",
        }
