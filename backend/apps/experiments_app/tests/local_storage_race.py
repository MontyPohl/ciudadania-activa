"""Carrera de primera inicialización de la raíz de objetos entre procesos (soporte test-only).

`race` lanza varios procesos que guardan a la vez en una raíz cuyo padre aún no existe. El
objetivo de cada hijo es la función top-level `save_in_process`, con argumentos picklables:
sirve con `spawn` (proceso nuevo: configura Django y aplica los settings S11 recibidos) y con
`fork`. Cada hijo devuelve cuántas veces corrió el inicializador canónico, el inode y el mtime
del marcador tras su guardado y si heredó la memoria del padre (`fork`) o arrancó limpio
(`spawn`); un error se devuelve por su tipo.

Al vencer el plazo, `race` mata y recoge a sus hijos y lanza `RaceTimeout`: un cuelgue nunca
es un resultado.

`python -m apps.experiments_app.tests.local_storage_race '<json>'` ejecuta la carrera con
`fork` desde un proceso nuevo y monohilo e imprime el resultado en JSON.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import queue
import signal
import sys
import threading
import time

#: Los settings que el hijo necesita para el storage local aislado.
SETTINGS = (
    "S11_LOCAL_STORAGE_ENABLED",
    "S11_EXPERIMENTS_ENABLED",
    "S11_ENVIRONMENT",
    "S11_DATABASE_URL",
    "REDIS_URL",
    "S11_OBJECT_ROOT",
    "S11_ARTIFACT_ROOT",
    "S11_BACKUP_ROOT",
    "S11_RESTORE_ROOT",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_STORAGE_BUCKET_PRIVATE",
)
BUCKET = "s11-reportes"
DJANGO_SETTINGS = "config.settings.test"
#: Lo fija `race` antes de lanzar los hijos: un hijo de `fork` lo hereda, uno de `spawn` no.
_PARENT = None


class RaceTimeout(AssertionError):
    def __init__(self, message: str, pids: list[int]):
        super().__init__(message)
        self.pids = pids


def settings_values(settings) -> dict[str, str]:
    return {name: str(getattr(settings, name)) for name in SETTINGS}


def _configure(values: dict[str, str]):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS)
    import django
    from django.conf import settings

    django.setup()
    for name, value in values.items():
        setattr(settings, name, value)
    from apps.experiments_app import local_storage

    return local_storage


def save_in_process(values, barrier, results, worker) -> None:
    try:
        local_storage = _configure(values)
        initialized = []
        real = local_storage.initialize_object_root
        local_storage.initialize_object_root = lambda c: initialized.append(1) or real(c)
        barrier.wait(timeout=30)
        local_storage.upload(BUCKET, f"reports/{worker}/img.png", b"png", "image/png")
        marker = os.lstat(os.path.join(values["S11_OBJECT_ROOT"], local_storage.OBJECT_ROOT_MARKER))
        results.put(
            {
                "worker": worker,
                "initialized": len(initialized),
                "marker": [marker.st_ino, marker.st_mtime_ns],
                "inherited_parent_memory": _PARENT is not None,
                "error": None,
            }
        )
    except BaseException as exc:  # noqa: BLE001 - el padre debe ver cualquier fallo
        results.put({"worker": worker, "error": type(exc).__name__})


def race(values, *, start_method: str, workers: int = 6, deadline: float = 60.0, parties=None):
    """Resultados y códigos de salida; `RaceTimeout` si no terminan todos dentro del plazo.

    `parties` sólo existe para probar el plazo: una barrera que nunca se completa.
    """
    global _PARENT
    _PARENT = os.getpid()
    context = multiprocessing.get_context(start_method)
    barrier, results = context.Barrier(parties or workers), context.Queue()
    processes = [
        context.Process(target=save_in_process, args=(values, barrier, results, w))
        for w in range(workers)
    ]
    outcomes = []
    limit = time.monotonic() + deadline
    try:
        for process in processes:
            process.start()
        while len(outcomes) < workers:
            try:
                outcomes.append(results.get(timeout=max(0.0, limit - time.monotonic())))
            except queue.Empty:
                break
        for process in processes:
            process.join(timeout=max(0.0, limit - time.monotonic()))
    finally:
        pending = [(p.pid, p.exitcode) for p in processes if p.pid and p.is_alive()]
        for process in processes:
            if process.pid and process.is_alive():
                os.kill(process.pid, signal.SIGKILL)
            if process.pid:
                process.join(timeout=10)
        results.close()
        results.join_thread()
    if pending or len(outcomes) < workers:
        raise RaceTimeout(
            f"carrera sin terminar: pendientes={pending} resultados={len(outcomes)}",
            [p.pid for p in processes if p.pid],
        )
    return {
        "start_method": start_method,
        "pids": [p.pid for p in processes],
        "exitcodes": [p.exitcode for p in processes],
        "outcomes": sorted(outcomes, key=lambda o: o["worker"]),
        "alive_after": [p.pid for p in processes if p.is_alive()],
    }


def main(argv: list[str]) -> int:
    """Control de `fork` desde un proceso nuevo y monohilo; el resultado va en JSON."""
    values = json.loads(argv[0])
    _configure(values)
    threads = threading.active_count()
    if threads != 1:
        print(json.dumps({"parent_threads_at_fork": threads}))
        return 2
    report = race(values, start_method="fork")
    report["parent_threads_at_fork"] = threads
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
