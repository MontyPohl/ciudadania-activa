"""Executor de EX-10 (fase 3A): un subcomando por paso irreversible, nunca uno monolítico.

`start` → `execute` (emisión y drain) → `finalize`, con `status`, `reconcile` (sólo
lectura) y `drain` (recuperación) aparte. `rehearsal-started` es el checkpoint exclusivo de
LOCAL_TEST: PREPARED → STARTED sin emitir, para ensayar el lifecycle antes de t0. Cada paso
comprueba su fase en la evidencia custodiada: no hay reintento de intentos, emisión parcial
ni reutilización de `run_id`.

Se ejecuta en un contenedor efímero del backend, dentro de la red Compose, con web y worker
arriba (salvo `status`). Salida: un JSON por stdout. Un rechazo va por stderr como JSON de
códigos, sin valores. Exit 0 éxito, 1 rechazo reintentable, 2 rechazo definitivo y 3 error
de uso, configuración o interno.
"""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.experiments_app import ex10_executor as executor
from apps.experiments_app.evidence import EvidenceError
from apps.experiments_app.runtime import evidence_store, valid_run_id

EXIT_REFUSED, EXIT_TERMINAL, EXIT_ERROR = 1, 2, 3


def _interrupt(number, frame):
    raise KeyboardInterrupt


class Command(BaseCommand):
    help = (
        "Executor de EX-10: start, execute, drain, reconcile, finalize, status y rehearsal-started."
    )
    requires_system_checks = []

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        parser.error = self._usage_error(parser)
        return parser

    def _usage_error(self, parser):
        """Un error de argumentos siempre sale con 3, sin eco de los valores recibidos.

        Cada subparser necesita el suyo: argparse sale con 2 (el código de un rechazo
        definitivo) y `CommandParser` lanza `CommandError` con 1 (el de uno reintentable).
        """

        def usage_error(message):
            code = "S11_EX10_USAGE: invalid-arguments"
            if getattr(parser, "called_from_command_line", False):
                self.stderr.write(code)
                sys.exit(EXIT_ERROR)
            raise CommandError(code, returncode=EXIT_ERROR)

        return usage_error

    def add_arguments(self, parser):
        actions = parser.add_subparsers(dest="action", required=True)
        # Fixed W06 OS entrypoint, never a general RPC/action/provider selector.
        bridge = actions.add_parser("bridge", help="Canal OS interno aprobado.")
        bridge.error = self._usage_error(bridge)
        start = actions.add_parser("start", help="Preflight en proceso y run_id nuevo.")
        start.error = self._usage_error(start)
        start.add_argument("--operational-context-stdin", action="store_true")
        start.add_argument("--expected-git-sha", required=True)
        start.add_argument("--source-root", required=True)
        start.add_argument("--nonce", required=True)
        start.add_argument("--evidence-mode", required=True, choices=list(executor.EXPERIMENT_IDS))
        start.add_argument("--acknowledge-not-demonstrated", action="append", default=[])
        start.add_argument("--confirm-new-run", action="store_true", required=True)
        for name in ("execute", "drain", "reconcile", "finalize"):
            step = actions.add_parser(name)
            step.error = self._usage_error(step)
            step.add_argument("--operational-context-stdin", action="store_true")
            step.add_argument("--run-id", required=True)
            step.add_argument("--expected-git-sha", required=True)
            if name == "execute":
                step.add_argument("--confirm-emission", action="store_true", required=True)
            if name == "finalize":
                step.add_argument("--abandon-reason")
                step.add_argument("--declare-executor-gone", action="store_true")
        rehearsal = actions.add_parser(
            "rehearsal-started", help="Sólo LOCAL_TEST: PREPARED → STARTED, sin emitir."
        )
        rehearsal.error = self._usage_error(rehearsal)
        rehearsal.add_argument("--run-id", required=True)
        rehearsal.add_argument("--expected-git-sha", required=True)
        rehearsal.add_argument("--confirm-local-rehearsal", action="store_true", required=True)
        status = actions.add_parser("status")
        status.error = self._usage_error(status)
        status.add_argument("--run-id", required=True)

    def handle(self, *args, **options):
        action = options["action"]
        run_id = options.get("run_id")
        if run_id is not None and not valid_run_id(run_id):
            raise CommandError("S11_EX10_USAGE: run-id", returncode=EXIT_ERROR)
        if action in {"execute", "rehearsal-started"}:
            # SIGTERM o SIGHUP del launcher se tratan como Ctrl-C: interrupción del operador.
            for number in (signal.SIGTERM, signal.SIGHUP):
                signal.signal(number, _interrupt)
        bridge_refusal = None
        try:
            if action == "bridge":
                code = executor.run_native_bridge()
                if type(code) is int and code == EXIT_TERMINAL:
                    bridge_refusal = CommandError(
                        "S11_EX10_NATIVE_WORKER_REFUSED", returncode=EXIT_TERMINAL
                    )
                    raise bridge_refusal
                if code != 0:
                    raise CommandError("S11_EX10_NATIVE_WORKER_FAILED", returncode=EXIT_ERROR)
                return
            if options.get("operational_context_stdin"):
                if action == "start" and options["evidence_mode"] == executor.LOCAL_TEST_MODE:
                    raise executor.Ex10Refusal(["local-test-official-context"], terminal=True)
                context = self.operational_context(options)
                if (
                    type(context) is not executor.ExecutorOperationalContext
                    or context.action != action
                ):
                    raise executor.Ex10Refusal(["operational-context-required"], terminal=True)
                options["_operational_context"] = context
            result = getattr(self, f"_{action.replace('-', '_')}")(options)
            if options.get("_operational_context") is not None:
                options["_operational_context"].persist_public_result(self._store(), result)
        except executor.Ex10Refusal as exc:
            context = options.get("_operational_context")
            if (
                type(context) is executor.ExecutorOperationalContext
                and action == "finalize"
                and options.get("declare_executor_gone") is True
                and exc.terminal is True
                and exc.reasons == ("run-finalized",)
            ):
                try:
                    context.persist_terminal_refusal(exc, declare_executor_gone=True)
                except Exception:
                    raise CommandError(
                        "S11_EX10_NATIVE_REFUSAL_UNQUALIFIED", returncode=EXIT_ERROR
                    ) from None
            payload = {"refused": True, "terminal": exc.terminal, "reasons": list(exc.reasons)}
            sys.stderr.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
            code = EXIT_TERMINAL if exc.terminal else EXIT_REFUSED
            raise CommandError("S11_EX10_REFUSED", returncode=code) from None
        except CommandError as exc:
            if exc is bridge_refusal and bridge_refusal is not None:
                raise
            raise CommandError("S11_EX10_INTERNAL: CommandError", returncode=EXIT_ERROR) from None
        except KeyboardInterrupt:
            # Tras un checkpoint: la fase custodiada dice qué queda (`status`).
            raise CommandError("S11_EX10_INTERRUPTED: see status", returncode=EXIT_ERROR) from None
        except (executor.Ex10ExecutorError, EvidenceError, ValueError) as exc:
            raise CommandError(
                f"S11_EX10_ERROR: {type(exc).__name__}", returncode=EXIT_ERROR
            ) from None
        except Exception as exc:  # noqa: BLE001 - sin traceback: puede llevar valores del entorno
            raise CommandError(
                f"S11_EX10_INTERNAL: {type(exc).__name__}", returncode=EXIT_ERROR
            ) from None
        if options.get("_operational_context") is None:
            self.stdout.write(json.dumps(result, sort_keys=True, ensure_ascii=False))

    def operational_context(self, options):
        """Fixed approved OS bootstrap; missing provision denies before probing."""
        try:
            native = executor.ExecutorNativeBootstrap()
            return native.build(
                options=options,
                store=self._store(),
                probes=self.probes() or executor.ExecutorProbes(),
            )
        except executor.ExecutorBridgeError as error:
            if error.code == "NATIVE_BOOTSTRAP_REQUIRED":
                raise executor.Ex10Refusal(
                    ["operational-context-bootstrap-required"], terminal=True
                ) from None
            raise

    def _start(self, options):
        return executor.start_run(
            self._store(),
            git_sha=options["expected_git_sha"],
            source_root=Path(options["source_root"]),
            nonce=options["nonce"],
            evidence_mode=options["evidence_mode"],
            timezone=settings.TIME_ZONE,
            acknowledged_not_demonstrated=options["acknowledge_not_demonstrated"],
            probes=self.probes(),
            operational_context=options.get("_operational_context"),
        )

    def _execute(self, options):
        return executor.execute_run(
            self._store(),
            run_id=options["run_id"],
            git_sha=options["expected_git_sha"],
            probes=self.probes(),
            operational_context=options.get("_operational_context"),
        )

    def _drain(self, options):
        return executor.run_drain_step(
            self._store(),
            run_id=options["run_id"],
            git_sha=options["expected_git_sha"],
            probes=self.probes() or executor.ExecutorProbes(),
            operational_context=options.get("_operational_context"),
        )

    def _reconcile(self, options):
        return executor.reconcile_run(
            self._store(),
            run_id=options["run_id"],
            git_sha=options["expected_git_sha"],
            probes=self.probes(),
            operational_context=options.get("_operational_context"),
        )

    def _finalize(self, options):
        return executor.finalize_run(
            self._store(),
            run_id=options["run_id"],
            git_sha=options["expected_git_sha"],
            abandon_reason=options["abandon_reason"],
            declare_executor_gone=options["declare_executor_gone"],
            probes=self.probes(),
            operational_context=options.get("_operational_context"),
        )

    def _rehearsal_started(self, options):
        return executor.rehearsal_started_run(
            self._store(),
            run_id=options["run_id"],
            git_sha=options["expected_git_sha"],
            probes=self.probes(),
        )

    def _status(self, options):
        return executor.run_status(self._store(), options["run_id"])

    @staticmethod
    def _store():
        # Un run de EX-10 abarca varios procesos: SHA256SUMS sólo se escribe al sellar.
        return evidence_store(rolling_checksums=False)

    def probes(self) -> executor.ExecutorProbes | None:
        """Punto de sustitución de los tests; en producción, los adapters reales."""
        return None
