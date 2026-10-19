"""Preparación operacional de EX-10 (fase 2B-3): un JSON por stdout, nunca un archivo.

`--confirm-isolated-reset` borra y reconstruye el estado S11 aislado hasta `READY_STATE`
(flush, catálogos, objetos, Redis 11, caché y los diez actores). `--inspect-only` sólo lee.
Uno de los dos es obligatorio: sin él no se hace nada. Se ejecuta con la base y Redis arriba
y con `backend`, `worker` y `beat` detenidos; el preflight corre después, ya con ellos vivos.

Exit: 0 PASS / READY_STATE, 1 FAIL / NOT_READY, 2 INCOMPLETE y 3 error de uso o de
configuración. stderr sólo lleva códigos saneados: nunca argumentos, URLs, rutas ni mensajes
de excepción. `--source-root` es, como en el preflight, un checkout Git completo en sólo
lectura.
"""

from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app import ex10_prepare


class Command(BaseCommand):
    help = "Prepara (o inspecciona) el estado S11 aislado de EX-10, sin run_id ni evidencia."
    requires_system_checks = []

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)

        def usage_error(message):
            # argparse saldría con 2 (= INCOMPLETE) y citaría el valor recibido. Django no
            # captura errores de parseo: desde la terminal se sale aquí mismo con 3.
            code = "S11_EX10_PREPARE_USAGE: invalid-arguments"
            if getattr(parser, "called_from_command_line", False):
                self.stderr.write(code)
                sys.exit(ex10_prepare.EXIT_USAGE)
            raise CommandError(code, returncode=ex10_prepare.EXIT_USAGE)

        parser.error = usage_error
        return parser

    def add_arguments(self, parser):
        parser.add_argument("--expected-git-sha", required=True)
        parser.add_argument("--source-root", required=True)
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--confirm-isolated-reset", action="store_true")
        mode.add_argument("--inspect-only", action="store_true")

    def handle(self, *args, **options):
        # Exactamente uno de los dos, y en `True`: `call_command(..., inspect_only=False)`
        # satisface el grupo de argparse, pero no autoriza ningún modo.
        flags = (options.get("confirm_isolated_reset") is True, options.get("inspect_only") is True)
        if flags == (True, False):
            mode = ex10_prepare.Mode.PREPARE
        elif flags == (False, True):
            mode = ex10_prepare.Mode.INSPECT
        else:
            raise CommandError("S11_EX10_PREPARE_USAGE: invalid-arguments", returncode=3)
        prepare_options = ex10_prepare.PrepareOptions(
            expected_git_sha=options["expected_git_sha"],
            source_root=Path(options["source_root"]),
            mode=mode,
        )
        try:
            report = ex10_prepare.run_prepare(prepare_options, self.probes())
        except ex10_prepare.PrepareError as exc:
            raise CommandError(f"S11_EX10_PREPARE_CONFIG: {exc.code}", returncode=3) from None
        except Exception as exc:  # noqa: BLE001 - un fallo interno nunca vuelca su mensaje
            raise CommandError(
                f"S11_EX10_PREPARE_INTERNAL_ERROR: {type(exc).__name__}", returncode=3
            ) from None
        self.stdout.write(report.to_bytes().decode("utf-8"), ending="")
        if report.exit_code:
            sys.exit(report.exit_code)

    def probes(self) -> ex10_prepare.PrepareProbes | None:
        """Punto de sustitución de los tests; en producción, los adapters reales."""
        return None
