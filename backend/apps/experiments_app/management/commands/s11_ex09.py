from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app import ex09
from apps.experiments_app.evidence import EvidenceError
from apps.experiments_app.runtime import evidence_store, valid_run_id


def _now() -> datetime:
    return datetime.now(UTC)


class Command(BaseCommand):
    help = (
        "Ejecutor S11-C3 de EX-09. Sólo el subcomando run-lot contacta al proveedor; "
        "se invoca a través de backend/scripts/s11_ex09.py en el contenedor efímero."
    )

    def add_arguments(self, parser):
        subcommands = parser.add_subparsers(dest="action", required=True)

        preflight = subcommands.add_parser("preflight", help="Comprobación estática, sin red.")
        preflight.add_argument("--lot", required=True, choices=list(ex09.LOTS))
        self._git_arguments(preflight)

        start = subcommands.add_parser("start", help="Asigna run_id y manifest inicial.")
        start.add_argument("--nonce", required=True)
        self._git_arguments(start)

        record = subcommands.add_parser("record", help="Atestación del operador (D4).")
        record.add_argument("--run-id", required=True)
        record.add_argument("--lot", required=True, choices=list(ex09.LOTS))
        record.add_argument("--event", required=True, choices=list(ex09.OPERATOR_EVENTS))
        record.add_argument("--observed-remaining", type=int)
        record.add_argument("--rpd-window")
        record.add_argument("--first-available-window", action="store_true")
        record.add_argument("--credential-fingerprint")
        record.add_argument("--container-absent", action="store_true")

        run_lot = subcommands.add_parser("run-lot", help="Ejecuta un lote contra el proveedor.")
        run_lot.add_argument("--run-id", required=True)
        run_lot.add_argument("--lot", required=True, choices=list(ex09.LOTS))
        self._git_arguments(run_lot)

        finalize = subcommands.add_parser("finalize", help="Decide y sella el run.")
        finalize.add_argument("--run-id", required=True)
        finalize.add_argument("--abandon-reason")
        self._git_arguments(finalize)

        status = subcommands.add_parser("status", help="Resumen de sólo lectura.")
        status.add_argument("--run-id", required=True)

    @staticmethod
    def _git_arguments(parser) -> None:
        parser.add_argument("--git-sha", required=True)
        parser.add_argument("--git-clean", action="store_true", required=True)

    def handle(self, *args, **options):
        action = options["action"]
        run_id = options.get("run_id")
        if run_id is not None and not valid_run_id(run_id):
            raise CommandError("run_id no tiene el formato S11 esperado.")
        if action != "run-lot" and ex09.read_credential(os.environ) is not None:
            raise CommandError(
                f"{ex09.PROVIDER_KEY_ENV} sólo puede existir en el proceso de run-lot."
            )
        try:
            result = getattr(self, f"_{action.replace('-', '_')}")(options)
        except ex09.Ex09Refusal as exc:
            payload = {"refused": True, "terminal": exc.terminal, "reasons": list(exc.reasons)}
            sys.stderr.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
            raise CommandError("EX-09 rechazó el paso antes de llamar al proveedor.") from exc
        except (ex09.Ex09Error, EvidenceError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, sort_keys=True, ensure_ascii=False))

    def _preflight(self, options):
        # Sólo imprime: una comprobación previa no crea run ni evidencia.
        return ex09.run_preflight(
            lot_id=options["lot"],
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            environ=os.environ,
            repo_root=Path(settings.REPO_ROOT),
            evidence_root=Path(settings.S11_ARTIFACT_ROOT),
            evidence_mode=ex09.OFFICIAL_MODE,
            require_credential=False,
        )

    def _start(self, options):
        return ex09.start_run(
            self._store(),
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            nonce=options["nonce"],
            evidence_mode=ex09.OFFICIAL_MODE,
            started_at=_now(),
            timezone=settings.TIME_ZONE,
        )

    def _record(self, options):
        return ex09.record_event(
            self._store(),
            run_id=options["run_id"],
            lot_id=options["lot"],
            event=options["event"],
            recorded_at=_now(),
            environ=os.environ,
            observed_remaining=options["observed_remaining"],
            rpd_window=options["rpd_window"],
            first_available_window=options["first_available_window"],
            credential_fingerprint_value=options["credential_fingerprint"],
            container_absent=options["container_absent"],
        )

    def _run_lot(self, options):
        return ex09.execute_lot(
            self._store(),
            run_id=options["run_id"],
            lot_id=options["lot"],
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            environ=os.environ,
            repo_root=Path(settings.REPO_ROOT),
            clock=_now,
            evidence_mode=ex09.OFFICIAL_MODE,
        )

    def _finalize(self, options):
        return ex09.finalize_run(
            self._store(),
            run_id=options["run_id"],
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            clock=_now,
            evidence_mode=ex09.OFFICIAL_MODE,
            abandon_reason=options["abandon_reason"],
        )

    def _status(self, options):
        return ex09.run_status(self._store(), options["run_id"], now=_now())

    @staticmethod
    def _store():
        # EX-09 abarca dos sesiones: SHA256SUMS sólo se escribe al finalizar (D3.3).
        return evidence_store(rolling_checksums=False)
