from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app import ex09_openai
from apps.experiments_app.evidence import EvidenceError
from apps.experiments_app.runtime import evidence_store, valid_run_id


def _now() -> datetime:
    return datetime.now(UTC)


def _yes_no(value: str) -> bool:
    if value not in {"yes", "no"}:
        raise ValueError("Se espera yes o no.")
    return value == "yes"


class Command(BaseCommand):
    help = (
        "Ejecutor S11-C3 de EX-09, instancia OpenAI (s11-ex09-openai-v1). Sólo el "
        "subcomando run contacta al proveedor; se invoca a través de "
        "backend/scripts/s11_ex09_openai.py en el contenedor efímero."
    )

    def add_arguments(self, parser):
        subcommands = parser.add_subparsers(dest="action", required=True)

        preflight = subcommands.add_parser("preflight", help="Comprobación local, sin red.")
        self._git_arguments(preflight)

        start = subcommands.add_parser("start", help="Asigna run_id y manifest inicial.")
        start.add_argument("--nonce", required=True)
        self._git_arguments(start)

        record = subcommands.add_parser("record", help="Atestación del operador.")
        record.add_argument("--run-id", required=True)
        record.add_argument("--event", required=True, choices=list(ex09_openai.OPERATOR_EVENTS))
        record.add_argument("--tier")
        record.add_argument("--rpm", type=int)
        record.add_argument("--tpm", type=int)
        record.add_argument("--rpd", type=int)
        record.add_argument("--credit-available", choices=["yes", "no"])
        record.add_argument("--spend-limit-reached", choices=["yes", "no"])
        record.add_argument("--credential-fingerprint")
        record.add_argument("--container-absent", action="store_true")

        run = subcommands.add_parser("run", help="Ejecuta la sesión contra el proveedor.")
        run.add_argument("--run-id", required=True)
        self._git_arguments(run)

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
        if action != "run" and os.environ.get(ex09_openai.PROVIDER_KEY_ENV, "").strip():
            raise CommandError(
                f"{ex09_openai.PROVIDER_KEY_ENV} sólo puede existir en el proceso de run."
            )
        try:
            result = getattr(self, f"_{action}")(options)
        except ex09_openai.Ex09OpenAiRefusal as exc:
            payload = {"refused": True, "terminal": exc.terminal, "reasons": list(exc.reasons)}
            sys.stderr.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
            raise CommandError("EX-09 rechazó el paso antes de llamar al proveedor.") from exc
        except (ex09_openai.Ex09OpenAiError, EvidenceError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, sort_keys=True, ensure_ascii=False))

    def _preflight(self, options):
        # Sólo imprime: el preflight local no crea run ni evidencia.
        return ex09_openai.run_preflight(
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            environ=os.environ,
            repo_root=Path(settings.REPO_ROOT),
            evidence_root=Path(settings.S11_ARTIFACT_ROOT),
            evidence_mode=ex09_openai.OFFICIAL_MODE,
        )

    def _start(self, options):
        return ex09_openai.start_run(
            evidence_store(),
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            nonce=options["nonce"],
            evidence_mode=ex09_openai.OFFICIAL_MODE,
            started_at=_now(),
            timezone=settings.TIME_ZONE,
        )

    def _record(self, options):
        credit = options["credit_available"]
        spend = options["spend_limit_reached"]
        return ex09_openai.record_event(
            evidence_store(),
            run_id=options["run_id"],
            event=options["event"],
            recorded_at=_now(),
            environ=os.environ,
            tier=options["tier"],
            rpm=options["rpm"],
            tpm=options["tpm"],
            rpd=options["rpd"],
            credit_available=_yes_no(credit) if credit else None,
            spend_limit_reached=_yes_no(spend) if spend else None,
            credential_fingerprint_value=options["credential_fingerprint"],
            container_absent=options["container_absent"],
        )

    def _run(self, options):
        return ex09_openai.execute_session(
            evidence_store(),
            run_id=options["run_id"],
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            environ=os.environ,
            repo_root=Path(settings.REPO_ROOT),
            clock=_now,
            evidence_mode=ex09_openai.OFFICIAL_MODE,
        )

    def _finalize(self, options):
        return ex09_openai.finalize_run(
            evidence_store(),
            run_id=options["run_id"],
            git_sha=options["git_sha"],
            git_clean=options["git_clean"],
            clock=_now,
            evidence_mode=ex09_openai.OFFICIAL_MODE,
            abandon_reason=options["abandon_reason"],
        )

    def _status(self, options):
        return ex09_openai.run_status(evidence_store(), options["run_id"], now=_now())
