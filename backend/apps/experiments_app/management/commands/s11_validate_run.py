from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.constants import ArtifactKind
from apps.experiments_app.redaction import contains_secret
from apps.experiments_app.runtime import evidence_store, valid_run_id, valid_scenario_id


class Command(BaseCommand):
    help = "Valida completitud, correlación y custodia antes de cerrar un run S11."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--scenario", required=True)

    def handle(self, *args, **options):
        run_id = options["run_id"]
        scenario_id = options["scenario"]
        if not valid_run_id(run_id) or not valid_scenario_id(scenario_id):
            raise CommandError("La correlación del run no es válida.")

        store = evidence_store()
        errors = store.verify(run_id)
        records = store.records(run_id)
        artifact_types = {record["artifact_type"] for record in records}
        required_artifacts = {
            "run-manifest-initial",
            "source-export",
            "harness-execution",
        }
        for missing in sorted(required_artifacts - artifact_types):
            errors.append(f"missing-artifact:{missing}")

        run_root = store.initialize_run(run_id)
        http_events = self._events(run_root, scenario_id, "http", errors)
        celery_events = self._events(run_root, scenario_id, "celery", errors)
        if not http_events:
            errors.append("missing-log:http")
        if not celery_events:
            errors.append("missing-log:celery")
        self._validate_http(http_events, errors)
        self._validate_celery(celery_events, errors)
        for stream, events in (("http", http_events), ("celery", celery_events)):
            for event in events:
                if event.get("run_id") != run_id or event.get("scenario_id") != scenario_id:
                    errors.append(f"correlation:{stream}")
                if contains_secret(event):
                    errors.append(f"secret:{stream}")

        if errors:
            raise CommandError(f"El run no supera la validación: {sorted(set(errors))}")
        store.write_checksums(run_id)
        self.stdout.write(
            self.style.SUCCESS(
                json.dumps(
                    {
                        "artifacts": len(records),
                        "celery_events": len(celery_events),
                        "http_events": len(http_events),
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "status": "PASS",
                    },
                    sort_keys=True,
                )
            )
        )

    @staticmethod
    def _events(run_root, scenario_id, stream, errors):
        path = run_root / ArtifactKind.LOG.value / scenario_id / f"{stream}.jsonl"
        if not path.is_file():
            return []
        try:
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        except (OSError, json.JSONDecodeError, TypeError):
            errors.append(f"invalid-log:{stream}")
            return []

    @staticmethod
    def _validate_http(events, errors):
        required = {
            "request_id",
            "endpoint",
            "method",
            "started_at",
            "finished_at",
            "duration_ms",
            "http_status",
        }
        if any(
            event.get("event") != "http.finished" or not required <= event.keys()
            for event in events
        ):
            errors.append("schema:http")

    @staticmethod
    def _validate_celery(events, errors):
        states: dict[str, set[str]] = {}
        for event in events:
            task_id = str(event.get("task_id") or "")
            states.setdefault(task_id, set()).add(str(event.get("event") or ""))
        for task_id, observed in states.items():
            if (
                not task_id
                or not {"celery.queued", "celery.started", "celery.finished"} <= observed
            ):
                errors.append(f"lifecycle:celery:{task_id or 'missing-id'}")
            if "celery.failed" in observed:
                errors.append(f"failed:celery:{task_id}")
