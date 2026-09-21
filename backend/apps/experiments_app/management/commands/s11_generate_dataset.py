from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.datasets import (
    FUNCTIONAL_DATASET_ID,
    LOAD_DATASET_ID,
    generate_dataset,
)


class Command(BaseCommand):
    help = "Genera un dataset sintético S11 determinista; no toca la base ni ejecuta escenarios."

    def add_arguments(self, parser):
        parser.add_argument("dataset_id", choices=[FUNCTIONAL_DATASET_ID, LOAD_DATASET_ID])
        parser.add_argument("--seed", type=int, default=20260921)
        parser.add_argument("--output", type=Path, required=True)

    def handle(self, *args, **options):
        dataset = generate_dataset(options["dataset_id"], seed=options["seed"])
        target = options["output"].resolve()
        if target.exists():
            raise CommandError("El output ya existe; los datasets versionados no se sobrescriben.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dataset.bytes)
        self.stdout.write(
            self.style.SUCCESS(
                f"dataset_id={dataset.dataset_id} seed={dataset.seed} "
                f"sha256={dataset.sha256} path={target}"
            )
        )
