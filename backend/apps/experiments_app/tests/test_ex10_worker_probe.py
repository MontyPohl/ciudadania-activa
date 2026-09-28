"""EX-10 fase 2B-2: probe de control contra un worker Celery real. LOCAL TEST.

Worker aislado de tests: settings `dev`, fake de IA, prefork con concurrencia 2 y el Redis
del proyecto Docker de tests en la base 11. Sin proveedores, sin base experimental y sin
ninguna tarea enviada: sólo el comando de control del probe. No usa el stack S11 oficial.

El registro del probe es el real: el worker importa `apps.experiments_app.tasks` al
autodescubrir tareas. Sin ese Redis (fuera de Docker) el módulo se salta.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery import Celery
from django.conf import settings
from kombu import messaging
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


@pytest.fixture(scope="module")
def worker():
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
    try:
        deadline = time.monotonic() + 90
        while not control_app.control.ping(timeout=0.5):
            assert process.poll() is None, "el worker de tests terminó al arrancar"
            assert time.monotonic() < deadline, "el worker de tests no respondió a tiempo"
        yield SimpleNamespace(
            process=process,
            control=pf.CeleryWorkerControl(app=control_app),
            app=control_app,
            redis=pf.ReadOnlyRedis(Redis.from_url(BROKER)),
            base=base,
        )
    finally:
        process.terminate()
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(10)
        control_app.close()
        shutil.rmtree(base)


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
