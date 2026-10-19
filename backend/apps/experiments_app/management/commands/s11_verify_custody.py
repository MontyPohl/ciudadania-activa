from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app.custody import (
    CustodyError,
    attest_custody_verification,
    build_source_snapshot,
    complete_custody_verification,
    prepare_custody_verification,
)
from apps.experiments_app.runtime import custody_manager

_APPROVED_ROOT_SETS = {
    (
        Path("/home/monty/proyectos/ciudadania-activa/var/s11-runs"),
        Path("/home/monty/proyectos/ciudadania-activa/var/s11-backup"),
        Path("/home/monty/proyectos/ciudadania-activa/var/s11-restore"),
    ),
    (Path("/s11-evidence"), Path("/s11-backup"), Path("/s11-restore")),
}
_APPROVED_SOURCE_ROOTS = {
    Path("/home/monty/proyectos/ciudadania-activa"),
    Path("/s11-source"),
}


class Command(BaseCommand):
    help = "Verifica P02/P03 con un canario sintético; no ejecuta escenarios S11-C."

    def add_arguments(self, parser):
        parser.add_argument("phase", choices=("prepare", "complete", "attest"))
        parser.add_argument("--verification-id", required=True)
        parser.add_argument("--source-root", required=True)
        parser.add_argument("--baseline-sha", required=True)
        parser.add_argument("--commit-sha")
        parser.add_argument(
            "--persistence-boundary",
            default="independent-process",
            choices=("independent-process", "container-recreated"),
        )

    def handle(self, *args, **options):
        if settings.S11_ALLOW_SENSITIVE_PERSISTENCE:
            raise CommandError("SENSITIVE debe permanecer deshabilitado para esta verificación.")
        roots = (
            Path(settings.S11_ARTIFACT_ROOT).resolve(),
            Path(settings.S11_BACKUP_ROOT).resolve(),
            Path(settings.S11_RESTORE_ROOT).resolve(),
        )
        approved = {tuple(path.resolve() for path in root_set) for root_set in _APPROVED_ROOT_SETS}
        if roots not in approved:
            raise CommandError(
                "Las raíces configuradas no coinciden con las ubicaciones aprobadas."
            )
        source_root = Path(options["source_root"]).resolve()
        approved_source_roots = {path.resolve() for path in _APPROVED_SOURCE_ROOTS}
        if source_root not in approved_source_roots:
            raise CommandError("La raíz de fuentes no coincide con una ubicación aprobada.")

        try:
            manager = custody_manager()
            source_snapshot = build_source_snapshot(
                source_root,
                baseline_sha=options["baseline_sha"],
            )
            if options["phase"] == "prepare":
                result = prepare_custody_verification(
                    manager,
                    verification_id=options["verification_id"],
                    source_snapshot=source_snapshot,
                )
            elif options["phase"] == "complete":
                result = complete_custody_verification(
                    manager,
                    verification_id=options["verification_id"],
                    persistence_boundary=options["persistence_boundary"],
                    source_snapshot=source_snapshot,
                )
            else:
                if not options["commit_sha"]:
                    raise CommandError("attest requiere --commit-sha.")
                result = attest_custody_verification(
                    manager,
                    verification_id=options["verification_id"],
                    source_snapshot=source_snapshot,
                    commit_sha=options["commit_sha"],
                    source_root=source_root,
                    baseline_sha=options["baseline_sha"],
                )
        except (CustodyError, RuntimeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
