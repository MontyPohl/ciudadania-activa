"""Preflight operacional de EX-10 (fase 2B-2): un JSON por stdout, nunca un archivo.

Exit: 0 PASS / READY_FOR_EXECUTOR_INTEGRATION, 1 FAIL / NOT_READY, 2 INCOMPLETE y 3 error
de uso o configuración del propio preflight. stderr sólo lleva códigos saneados: nunca
argumentos, URLs, rutas ni mensajes de excepción.

`--source-root` debe apuntar a un checkout Git completo, legible y montado en sólo
lectura: su propio `.git` resoluble, la raíz del árbol de trabajo, historia completa y
ninguna vista parcial (sparse, skip-worktree, assume-unchanged, excludes locales o rutas
ausentes). El `/s11-source` del overlay S11 es parcial y no sirve: con él, HEAD y limpieza
no se infieren y el resultado es INCOMPLETE. No existe `--git-clean`: Git se inspecciona.

Un check NOT_RUN no corrió porque un prerrequisito suyo quedó FAIL o INCOMPLETE; nunca es
un resultado global ni un salto silencioso.
"""

from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app import ex10_preflight

_MIN_TIMEOUT, _MAX_TIMEOUT = 0.5, 30.0


class Command(BaseCommand):
    help = "Preflight operacional de EX-10: comprueba el stack sin carga, run_id ni evidencia."
    requires_system_checks = []

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)

        def usage_error(message):
            # argparse saldría con 2 (= INCOMPLETE) y citaría el valor recibido. Django no
            # captura errores de parseo: desde la terminal se sale aquí mismo con 3.
            code = "S11_EX10_PREFLIGHT_USAGE: invalid-arguments"
            if getattr(parser, "called_from_command_line", False):
                self.stderr.write(code)
                sys.exit(ex10_preflight.EXIT_USAGE)
            raise CommandError(code, returncode=ex10_preflight.EXIT_USAGE)

        parser.error = usage_error
        return parser

    def add_arguments(self, parser):
        parser.add_argument("--expected-git-sha", required=True)
        parser.add_argument("--source-root", required=True)
        parser.add_argument(
            "--probe-timeout", type=float, default=ex10_preflight.PROBE_TIMEOUT_SECONDS
        )

    def handle(self, *args, **options):
        timeout = options["probe_timeout"]
        if not _MIN_TIMEOUT <= timeout <= _MAX_TIMEOUT:
            raise CommandError("S11_EX10_PREFLIGHT_USAGE: probe-timeout-range", returncode=3)
        preflight_options = ex10_preflight.PreflightOptions(
            expected_git_sha=options["expected_git_sha"],
            source_root=Path(options["source_root"]),
            probe_timeout=timeout,
        )
        try:
            report = ex10_preflight.run_preflight(preflight_options, self.probes())
        except ex10_preflight.PreflightError as exc:
            raise CommandError(f"S11_EX10_PREFLIGHT_CONFIG: {exc.code}", returncode=3) from None
        except Exception as exc:  # noqa: BLE001 - un fallo interno nunca vuelca su mensaje
            raise CommandError(
                f"S11_EX10_PREFLIGHT_INTERNAL_ERROR: {type(exc).__name__}", returncode=3
            ) from None
        self.stdout.write(report.to_bytes().decode("utf-8"), ending="")
        if report.exit_code:
            sys.exit(report.exit_code)

    def probes(self) -> ex10_preflight.Probes | None:
        """Punto de sustitución de los tests; en producción, los adapters reales."""
        return None
