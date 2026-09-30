"""Escenarios con interrupción asíncrona de EX-10 en un proceso hijo. LOCAL TEST.

Una interrupción real (`_thread.interrupt_main` o SIGINT) entregada al proceso que ejecuta
coverage.py puede caer dentro de su lock de datos (`Collector.lock_data`, que el tracer llama
en cada llamada a código medido): el `KeyboardInterrupt` sale con el lock tomado y el proceso
se bloquea (COVERAGE_TRACER_INTERRUPT_DEADLOCK). Por eso estos escenarios corren en un hijo
sin coverage: el hijo recibe la interrupción real, con hilos, barrera y generador reales, y
el proceso de pytest nunca la recibe.

El hijo comprueba las mismas propiedades que el test y devuelve una única línea JSON con
hechos técnicos saneados: nunca tokens, cuerpos, imágenes, coordenadas ni tracebacks. Un
hijo que no termina a tiempo, sale con otro código, muere por una señal o imprime otra cosa
es un fallo del test, nunca un PASS.

Uso del hijo: python -m apps.experiments_app.tests.ex10_signal_child ESCENARIO PARAMS_JSON
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "s11-ex10-signal-child-v1"
#: Plazo firme del hijo. Un escenario sano tarda menos de 2 s; vencerlo es FAIL.
CHILD_TIMEOUT_SECONDS = 60.0
#: El hijo vuelca sus hilos y sale antes de que el padre lo mate (diagnóstico en stderr).
CHILD_WATCHDOG_SECONDS = 45.0
EXIT_ASSERTION, EXIT_ERROR, EXIT_USAGE = 1, 2, 3
#: Variables con las que pytest-cov (`pytest-cov.pth`) o coverage (`a1_coverage.pth`)
#: trazarían al hijo: con ellas volvería el mismo riesgo dentro de él.
_COVERAGE_ENV = re.compile(r"^(COV_CORE_.*|COVERAGE_PROCESS_START|COVERAGE_RCFILE)$")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+")
_BACKEND = Path(__file__).resolve().parents[3]


class ChildFailure(AssertionError):
    """El hijo no produjo un resultado válido. `reason` es un código; nunca un valor."""

    def __init__(self, reason: str, stderr: str = ""):
        self.reason = reason
        super().__init__(f"ex10 signal child: {reason}\n{_sanitized_tail(stderr)}")


def _sanitized_tail(text: str, lines: int = 40) -> str:
    """Las últimas líneas de stderr, para el log del test, sin nada con forma de token."""
    tail = text.splitlines()[-lines:]
    return "\n".join(_JWT.sub("[REDACTED]", line)[:300] for line in tail)


def child_environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _COVERAGE_ENV.match(k)}
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_BACKEND), env.get("PYTHONPATH", "")) if p)
    return env


def run_signal_scenario(
    name: str, *, timeout: float = CHILD_TIMEOUT_SECONDS, **params: Any
) -> dict[str, Any]:
    """Corre `name` en un hijo aislado y devuelve su resultado saneado, o lanza ChildFailure.

    El hijo va en su propio grupo de procesos: un timeout mata el grupo entero y lo recoge,
    sin zombis ni nietos. Un resultado se acepta sólo si es exactamente una línea JSON del
    esquema, del escenario pedido, y el hijo salió con 0.
    """
    command = [
        sys.executable,
        "-m",
        "apps.experiments_app.tests.ex10_signal_child",
        name,
        json.dumps(params, sort_keys=True),
    ]
    proc = subprocess.Popen(
        command,
        cwd=_BACKEND,
        env=child_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        _, stderr = proc.communicate()
        raise ChildFailure("timeout", stderr) from None
    finally:
        if proc.poll() is None:  # una excepción del propio padre: nunca un hijo huérfano
            _kill_group(proc)
            proc.communicate()
    if proc.returncode < 0:
        raise ChildFailure(f"signal:{signal.Signals(-proc.returncode).name}", stderr)
    if proc.returncode != 0:
        raise ChildFailure(f"exit:{proc.returncode}", stderr)
    lines = stdout.splitlines()
    try:
        (line,) = lines
        payload = json.loads(line)
    except ValueError:
        raise ChildFailure("stdout-invalid", stderr) from None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "scenario", "result"}
        or payload["schema"] != SCHEMA
        or payload["scenario"] != name
        or not isinstance(payload["result"], dict)
    ):
        raise ChildFailure("stdout-invalid", stderr)
    return payload["result"]


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# --- Lado del hijo ---------------------------------------------------------------------------


def _block_network() -> None:
    """Como el fixture `no_network`: cualquier conexión real hace fallar el escenario."""
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("LOCAL TEST / NON-EXPERIMENTAL: la red está prohibida.")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked


def _selftest(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Escenarios que sólo prueban el runner: cada uno falla de una forma concreta."""
    import threading
    import time

    if name == "selftest-ok":
        return {
            "python_threads": threading.active_count(),
            "coverage_loaded": "coverage" in sys.modules,
            "trace_active": sys.gettrace() is not None,
        }
    if name == "selftest-exit":
        sys.exit(params.get("code", 7))
    if name == "selftest-hang":
        # Un hijo colgado con un nieto en su grupo: el padre debe matar a ambos.
        grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        print(json.dumps({"grandchild": grandchild.pid}), file=sys.stderr, flush=True)
        time.sleep(600)
    if name == "selftest-signal":
        os.kill(os.getpid(), signal.SIGKILL)
    if name == "selftest-garbage":
        print("not json")
        return {"never": True}
    if name == "selftest-leak":
        # Un secreto con forma de JWT en stderr nunca llega entero al mensaje del padre.
        print("eyJhbGciOiJIUzI1NiJ9.eyJsZWFrIjoidGVzdCJ9.c2lnbmF0dXJl", file=sys.stderr)
        sys.exit(1)
    raise SystemExit(EXIT_USAGE)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return EXIT_USAGE
    name, params = argv[0], json.loads(argv[1])
    import faulthandler

    faulthandler.dump_traceback_later(CHILD_WATCHDOG_SECONDS, exit=True)
    if name.startswith("selftest-"):
        result = _selftest(name, params)
    else:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
        import django

        django.setup()
        _block_network()
        from apps.experiments_app.tests import test_ex10_load as scenarios

        scenario = scenarios.SIGNAL_SCENARIOS.get(name)
        if scenario is None:
            return EXIT_USAGE
        try:
            result = scenario(**params)
        except AssertionError:
            # El traceback va a stderr (el padre lo sanea); stdout no lleva nada.
            import traceback

            traceback.print_exc()
            return EXIT_ASSERTION
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps({"schema": SCHEMA, "scenario": name, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except SystemExit:
        raise
    except BaseException:
        import traceback

        traceback.print_exc()
        code = EXIT_ERROR
    sys.exit(code)
