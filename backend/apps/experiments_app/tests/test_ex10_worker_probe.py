"""EX-10 fase 2B-2: probe de control contra un worker Celery real. LOCAL TEST.

Worker aislado de tests: settings `dev`, fake de IA, prefork con concurrencia 2 y el Redis
del proyecto Docker de tests en la base 11. Sin proveedores, sin base experimental y sin
ninguna tarea enviada: sólo el comando de control del probe. No usa el stack S11 oficial.

El teardown del fixture cierra los pools globales de kombu que contaminó la app de control:
`Celery.close()` sólo suelta su referencia, y el canal Redis con un BRPOP de respuesta
pendiente bloqueaba a cualquier hijo `fork` posterior del mismo proceso de pytest (sus hooks
after-fork leen del socket heredado). Al final se comprueba que no queda ese residuo y que una
app nueva con el mismo broker obtiene un pool utilizable.

El registro del probe es el real: el worker importa `apps.experiments_app.tasks` al
autodescubrir tareas. Sin ese Redis (fuera de Docker) el módulo se salta.
"""

import contextlib
import multiprocessing.util
import os
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery import Celery
from django.conf import settings
from kombu import messaging, pools
from kombu.transport import redis as kombu_redis
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from apps.experiments_app import ex10_load
from apps.experiments_app import ex10_preflight as pf
from apps.experiments_app import ex10_worker_probe as wp

BROKER = "redis://redis:6379/11"
ENDPOINT = {"scheme": "redis", "host": "redis", "db": 11, "matches_protocol": True}


def _redis_reachable() -> bool:
    try:
        return bool(Redis.from_url(BROKER, socket_connect_timeout=1, socket_timeout=1).ping())
    except (RedisError, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _redis_reachable(), reason="Requiere el Redis aislado del proyecto Docker de tests."
)


def release_control_app(app, worker_process) -> None:
    """Cierra la app de control y los pools globales de kombu que contaminó.

    `Celery.close()` sólo suelta su referencia: el `ConnectionPool` sigue en
    `kombu.pools.connections` (compartido por clave de broker) con la conexión cuyo canal tiene
    un BRPOP de respuesta pendiente. `kombu.pools.reset()` cierra esos pools y los retira de los
    grupos globales, así que una app posterior obtiene uno nuevo. Un cierre acotado con
    `force_close_all(close_pool=False)` vaciaba la cola del pool compartido sin cerrarlo y el
    siguiente `acquire` bloqueante de cualquier app con el mismo broker esperaba para siempre.
    """
    if worker_process.poll() is None:
        raise RuntimeError("Los pools de control se liberan con el worker ya detenido.")
    try:
        app.close()
    finally:
        pools.reset()


def assert_a_new_app_gets_a_new_pool(used) -> None:
    """Tras la limpieza, el pool usado está cerrado y una app nueva con el mismo broker recibe otro.

    Celery adquiere con `acquire(block=True)` sin plazo: si el pool usado siguiera registrado y
    abierto pero vacío, esa adquisición esperaría para siempre. Cerrado, falla en el acto.
    """
    assert used._closed
    app = Celery("ex10-probe-tests-reuse", broker=BROKER, set_as_current=False)
    try:
        assert app.pool is not used
        app.pool.acquire(block=True, timeout=5).release()
    finally:
        app.close()


@contextlib.contextmanager
def control_session(app, worker_process):
    """La app de control del fixture; al salir, aunque algo falle, libera sus pools kombu."""
    try:
        yield app
    finally:
        release_control_app(app, worker_process)


def after_fork_hooks_since(before: set) -> list:
    """Los hooks after-fork de `multiprocessing` registrados después de `before`."""
    registry = multiprocessing.util._afterfork_registry
    return [(key, obj) for key, obj in list(registry.items()) if key not in before]


def pending_brpop_channels(hooks: list) -> list[str]:
    """Canales Redis de kombu abiertos con un BRPOP de respuesta pendiente."""
    return [
        repr(obj)
        for _, obj in hooks
        if isinstance(obj, kombu_redis.Channel) and not obj.closed and obj._in_poll
    ]


def fork_blocking_hooks(hooks: list, deadline: float = 10.0) -> list[str]:
    """Los hooks que no terminan en un hijo de `fork` dentro del plazo.

    Un hijo de `multiprocessing` con `fork` los ejecuta al arrancar. Aquí corren en un hijo de
    `os.fork`, que no ejecuta ningún otro, avisando por un pipe tras cada uno; al vencer el plazo
    el hijo se mata y se devuelven los que quedaron sin terminar.
    """
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - hijo
        os.close(read)
        try:
            for (_, _, hook), obj in hooks:
                hook(obj)
                os.write(write, b".")
        finally:
            os._exit(0)
    os.close(write)
    done, limit = 0, time.monotonic() + deadline
    try:
        while done < len(hooks):
            ready, _, _ = select.select([read], [], [], max(0.0, limit - time.monotonic()))
            chunk = os.read(read, 64) if ready else b""
            if not chunk:
                break
            done += len(chunk)
    finally:
        os.close(read)
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    return [f"{hook.__module__}.{hook.__qualname__}" for (_, _, hook), _ in hooks[done:]]


def stop_worker(process) -> None:
    process.terminate()
    try:
        process.wait(30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(10)


@pytest.fixture(scope="module")
def worker():
    hooks_before = set(multiprocessing.util._afterfork_registry)
    base = Path(settings.BASE_DIR) / ".s11-test-artifacts" / "ex10-worker-probe"
    if base.exists():
        shutil.rmtree(base)
    (base / "objects").mkdir(parents=True)
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.dev",
        "REDIS_URL": BROKER,
        "S11_EXPERIMENTS_ENABLED": "true",
        "S11_ENVIRONMENT": "isolated-experiment",
        "S11_DATABASE_URL": "postgis://ciudadania@db:5432/ciudadania_s11_worker_probe",
        "S11_OBJECT_ROOT": str(base / "objects"),
        "S11_FAKE_AI_ENABLED": "true",
        "AI_CLASSIFICATION_ENABLED": "false",
    }
    # El orden de salida es el inverso al de registro: worker, Redis, app de control y pools,
    # directorio. Cada paso corre aunque falle otro y la primera excepción se conserva.
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(shutil.rmtree, base)
        process = subprocess.Popen(
            ["celery", "-A", "config", "worker", "--loglevel=WARNING", "--concurrency=2"],
            cwd=settings.BASE_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # App de control propia: Celery publica los comandos por el broker de la app.
        control_app = Celery("ex10-probe-tests", broker=BROKER, set_as_current=False)
        control_app.conf.accept_content = ["json"]
        cleanup.enter_context(control_session(control_app, process))
        redis = Redis.from_url(BROKER)
        cleanup.callback(redis.close)
        cleanup.callback(stop_worker, process)
        deadline = time.monotonic() + 90
        while not control_app.control.ping(timeout=0.5):
            assert process.poll() is None, "el worker de tests terminó al arrancar"
            assert time.monotonic() < deadline, "el worker de tests no respondió a tiempo"
        used_pool = control_app.pool
        yield SimpleNamespace(
            process=process,
            control=pf.CeleryWorkerControl(app=control_app),
            app=control_app,
            redis=pf.ReadOnlyRedis(redis),
            base=base,
        )
    # Regresión de la fuga: el pool usado queda cerrado, una app nueva recibe otro y nada de lo
    # que se registró desde el inicio del fixture deja un BRPOP heredable.
    assert_a_new_app_gets_a_new_pool(used_pool)
    hooks = after_fork_hooks_since(hooks_before)
    assert pending_brpop_channels(hooks) == []
    assert fork_blocking_hooks(hooks) == []


def probe_twice(worker):
    nonces = [f"{n:032x}" for n in (1, 2)]
    return [
        [wp.validate_reply(reply, nonce) for reply in worker.control.probe(nonce, 3.0)]
        for nonce in nonces
    ]


def test_two_probes_reach_exactly_one_stable_and_correct_worker(worker):
    first, second = probe_twice(worker)
    assert len(first) == len(second) == 1
    facts = first[0]
    assert facts == second[0]  # mismo nodo, proceso, pool y configuración
    assert facts["process"]["pid"] > 0 and facts["node"].startswith("celery@")
    assert facts["pool"]["max_concurrency"] == ex10_load.WORKER_CONCURRENCY
    assert facts["task_always_eager"] is False
    assert facts["broker"] == ENDPOINT and facts["result_backend"] == ENDPOINT
    assert facts["fake_ai_enabled"] is True
    assert facts["ai_adapter"] == ex10_load.AI_MODEL_NAME
    assert facts["classify_task_registered"] is True


def test_the_control_plane_holds_no_task_result_or_unknown_key(worker):
    before = pf.classify_broker(worker.redis)
    probe_twice(worker)
    after = pf.classify_broker(worker.redis)
    for snapshot in (before, after):
        assert (snapshot.dynamic, snapshot.task_messages, snapshot.unknown) == (0, 0, 0)
        assert snapshot.unclassifiable == ()
    assert after.static_bindings >= 1  # `_kombu.binding.*` del worker
    assert worker.redis.llen("celery") == 0


def test_the_probes_publish_nothing_under_the_guard(worker):
    guard = pf.SideEffectGuard(protected_roots=(worker.base,), allowed_hosts=frozenset({"redis"}))
    with guard:
        results = probe_twice(worker)
    assert guard.violations == [] and all(len(result) == 1 for result in results)
    keys = [key.decode() for key in worker.redis.scan_iter(match="celery-task-meta-*")]
    assert keys == []


def test_an_unknown_command_on_the_real_worker_is_rejected(worker):
    replies = worker.app.control.broadcast("s11_ex10_absent_probe", reply=True, timeout=2.0)
    assert len(replies) == 1
    with pytest.raises(wp.ReplyRejected, match="worker-probe-not-registered"):
        wp.validate_reply(replies[0], "0" * 32)


def test_a_publish_connection_error_is_never_republished(worker, monkeypatch):
    attempts = []
    real_publish = messaging.Producer._publish

    def dropped_once(self, *args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RedisConnectionError("conexión perdida al publicar")
        return real_publish(self, *args, **kwargs)

    monkeypatch.setattr(messaging.Producer, "_publish", dropped_once)
    with pytest.raises(pf.ControlUnavailable):
        worker.control.probe("0" * 32, 1.0)
    assert len(attempts) == 1  # ni un segundo envío del comando
    # Control: el `broadcast` estándar de Celery sí lo habría republicado.
    attempts.clear()
    worker.app.control.broadcast("s11_ex10_absent_probe", reply=True, timeout=1.0)
    assert len(attempts) == 2


# --- Liberación de la app de control (sin Redis real) --------------------------------------


class FakeApp:
    def __init__(self, error=None):
        self.error, self.closes = error, 0

    def close(self):
        self.closes += 1
        if self.error:
            raise self.error


STOPPED = SimpleNamespace(poll=lambda: 0)


@pytest.fixture
def resets(monkeypatch):
    calls = []
    monkeypatch.setattr(pools, "reset", lambda: calls.append(1))
    return calls


def test_a_failure_inside_the_session_still_resets_the_pools_and_keeps_the_error(resets):
    app = FakeApp()
    with pytest.raises(LookupError, match="fallo original"):
        with control_session(app, STOPPED):
            raise LookupError("fallo original")
    assert app.closes == 1 and resets == [1]


def test_the_pools_are_reset_even_if_closing_the_app_fails(resets):
    app = FakeApp(error=OSError("cierre de la app"))
    with pytest.raises(OSError, match="cierre de la app"):
        release_control_app(app, STOPPED)
    assert resets == [1]


def test_the_pools_are_not_reset_while_the_worker_runs(resets):
    app = FakeApp()
    with pytest.raises(RuntimeError, match="worker ya detenido"):
        release_control_app(app, SimpleNamespace(poll=lambda: None))
    assert app.closes == 0 and resets == []


def children() -> set[int]:
    """Los PID cuyo padre es este proceso, incluidos los zombis sin recoger."""
    found = set()
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            with contextlib.suppress(OSError):
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == os.getpid():
                    found.add(int(entry.name))
    return found


def test_the_fork_probe_reports_a_hook_that_blocks_and_reaps_the_child():
    before = children()
    read, write = os.pipe()  # el hijo hereda el extremo de escritura: su lectura nunca vuelve
    try:
        hooks = [((0, 0, _noop), None), ((1, 0, _read_one_byte), read)]
        started = time.monotonic()
        blocked = fork_blocking_hooks(hooks, deadline=2.0)
    finally:
        os.close(read)
        os.close(write)
    assert blocked == [f"{__name__}._read_one_byte"]
    assert time.monotonic() - started < 10
    assert children() == before


def test_the_fork_probe_passes_hooks_that_return():
    assert fork_blocking_hooks([((0, 0, _noop), None), ((1, 0, _noop), None)], deadline=5.0) == []


def _read_one_byte(descriptor):
    os.read(descriptor, 1)


def _noop(_):
    return None
