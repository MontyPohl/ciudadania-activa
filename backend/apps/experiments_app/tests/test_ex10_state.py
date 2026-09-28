"""EX-10 fase 2B-1: reset y estados ZERO/READY. LOCAL TEST / NON-EXPERIMENTAL.

Base de tests de Django, Redis en memoria inyectado y raíces en `.s11-test-artifacts/`
(`verify_isolation` rechaza `/tmp`). Nunca se usa el stack S11, `var/s11-runs` ni un run
oficial, y no se hace ningún request a `/api/v1/reports/`.
"""

import base64
import fnmatch
import hashlib
import json
import shutil
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.management import call_command
from django.db import OperationalError
from kombu.transport.redis import Channel
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from rest_framework.throttling import ScopedRateThrottle

from apps.admin_app.models import Category
from apps.ai_client_app.models import AiClassification
from apps.auth_app.enums import UserRole
from apps.core.enums import ReportSource, ReportStatus
from apps.core.services import audit
from apps.experiments_app import ex10_fixture, ex10_load, runtime
from apps.experiments_app import ex10_state as st
from apps.experiments_app.isolation import initialize_object_root
from apps.reports_app.enums import MediaType
from apps.reports_app.models import Report, ReportMedia
from apps.rewards_app.services import ledger

User = get_user_model()
Status = st.CheckStatus
DECLARED_DB = "ciudadania_s11_unit"


class FakeRedis:
    """Un DB lógico de Redis en memoria: clave → (tipo, valor). Una instancia por test."""

    def __init__(self):
        self.data = {}
        self.down = False
        self.flushes = 0

    def put(self, name, kind, value):
        """Siembra de los tests; no forma parte de `RedisLike`."""
        self.data[name] = (kind, value)

    def _up(self):
        if self.down:
            raise RedisConnectionError("fake redis down")

    def _sized(self, name, kind):
        self._up()
        stored, value = self.data.get(name, (kind, ()))
        if stored != kind:
            raise ResponseError("WRONGTYPE")
        return len(value)

    def ping(self):
        self._up()
        return True

    def llen(self, name):
        return self._sized(name, "list")

    def hlen(self, name):
        return self._sized(name, "hash")

    def zcard(self, name):
        return self._sized(name, "zset")

    def scan_iter(self, match, count):
        self._up()
        return iter(sorted(k for k in self.data if fnmatch.fnmatchcase(k, match)))

    def flushdb(self):
        self._up()
        self.data.clear()
        self.flushes += 1


@pytest.fixture
def roots(settings, request):
    base = Path.cwd() / ".s11-test-artifacts" / f"ex10-state-{request.node.name}"
    shutil.rmtree(base, ignore_errors=True)
    evidence = {name: base / "evidence" / name for name in ("runs", "backup", "restore")}
    for path in evidence.values():
        path.mkdir(parents=True)
        (path / "SENTINEL").write_bytes(b"historical-evidence")
    settings.S11_EXPERIMENTS_ENABLED = True
    settings.S11_ENVIRONMENT = "isolated-experiment"
    settings.S11_DATABASE_URL = f"postgis://ciudadania@db:5432/{DECLARED_DB}"
    settings.REDIS_URL = "redis://redis:6379/11"
    settings.S11_OBJECT_ROOT = base / "objects"
    settings.S11_ARTIFACT_ROOT = evidence["runs"]
    settings.S11_BACKUP_ROOT = evidence["backup"]
    settings.S11_RESTORE_ROOT = evidence["restore"]
    yield SimpleNamespace(base=base, objects=base / "objects", evidence=evidence)
    # Sin `ignore_errors`: una raíz que no se pudiera borrar quedaría para el test siguiente.
    shutil.rmtree(base)


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def env(roots, redis, monkeypatch):
    # Simula estar conectado a la base aislada declarada: la guarda real se prueba aparte.
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    initialize_object_root(runtime.isolation_config())
    cache.clear()
    yield st.approve_environment(redis_factory=lambda url: redis)
    # La cache de tests es LocMem, del proceso: ninguna clave de throttle sobrevive al test.
    cache.clear()


@pytest.fixture
def seeded(db):
    call_command("seed_catalogs", verbosity=0)


@pytest.fixture
def ready(seeded, env):
    return ex10_fixture.prepare_fixture()


def result_of(report, name):
    return report.by_name()[name].result


def make_report(user=None):
    user = user or User.objects.get(pk=ex10_fixture.actor_specs()[0].user_id)
    category = Category.objects.get(slug="baches")
    return Report.objects.create(
        citizen=user,
        category=category,
        description="LOCAL-TEST",
        status=ReportStatus.CREADO,
        priority=category.default_priority,
        source=ReportSource.PLATFORM,
        location=Point(-57.5, -25.3, srid=4326),
    )


# --- Guardas --------------------------------------------------------------------------------


def test_approval_rejects_everything_outside_the_isolated_environment(roots, settings, monkeypatch):
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    cases = [
        ("S11_EXPERIMENTS_ENABLED", False, "ISOLATION_REJECTED"),
        ("S11_ENVIRONMENT", "", "ISOLATION_REJECTED"),
        (
            "S11_DATABASE_URL",
            "postgis://ciudadania@db:5432/ciudadania_activa",
            "ISOLATION_REJECTED",
        ),
        (
            "S11_DATABASE_URL",
            "postgis://u@db.supabase.co:5432/ciudadania_s11_x",
            "ISOLATION_REJECTED",
        ),
        ("REDIS_URL", "redis://redis:6379/0", "ISOLATION_REJECTED"),
        ("REDIS_URL", "redis://cache.heroku.example:6379/11", "ISOLATION_REJECTED"),
    ]
    for name, value, code in cases:
        original = getattr(settings, name)
        setattr(settings, name, value)
        with pytest.raises(st.Ex10StateError) as rejected:
            st.approve_environment(redis_factory=lambda url: FakeRedis())
        assert rejected.value.code == code, name
        assert "supabase" not in str(rejected.value) and "heroku" not in str(rejected.value)
        setattr(settings, name, original)


def test_the_connected_database_must_be_the_declared_one(roots):
    # Sin simulación: la base de tests no es la aislada declarada.
    with pytest.raises(st.Ex10StateError, match="DATABASE_NOT_APPROVED"):
        st.approve_environment(redis_factory=lambda url: FakeRedis())


def test_storage_outside_an_approved_root_is_rejected(roots, settings, monkeypatch, tmp_path):
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    settings.S11_OBJECT_ROOT = tmp_path / "objects"  # /tmp: verify_isolation lo rechaza
    with pytest.raises(st.Ex10StateError, match="ISOLATION_REJECTED"):
        st.approve_environment(redis_factory=lambda url: FakeRedis())
    settings.S11_OBJECT_ROOT = roots.evidence["runs"] / "objects"  # dentro de la evidencia
    with pytest.raises(st.Ex10StateError, match="STORAGE_OVERLAPS_EVIDENCE"):
        st.approve_environment(redis_factory=lambda url: FakeRedis())
    settings.S11_OBJECT_ROOT = roots.base / "evidence"  # contiene la evidencia
    with pytest.raises(st.Ex10StateError, match="STORAGE_OVERLAPS_EVIDENCE"):
        st.approve_environment(redis_factory=lambda url: FakeRedis())


def test_a_symlinked_object_root_is_rejected(roots, settings, monkeypatch):
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    real = roots.base / "real-objects"
    real.mkdir()
    link = roots.base / "linked-objects"
    link.symlink_to(real, target_is_directory=True)
    settings.S11_OBJECT_ROOT = link
    with pytest.raises(st.Ex10StateError, match="STORAGE_SYMLINK"):
        st.approve_environment(redis_factory=lambda url: FakeRedis())


def test_approved_environment_repr_holds_no_url_path_or_client(env):
    text = repr(env)
    assert text == f"ApprovedEnvironment(database={DECLARED_DB}, redis_db=11)"
    assert "redis://" not in text and ".s11-test-artifacts" not in text


# --- ZERO_STATE y READY_STATE --------------------------------------------------------------


def test_zero_state_passes_with_catalogs_and_nothing_else(seeded, env):
    report = st.inspect_zero_state(env)
    assert report.overall is Status.PASS, [c for c in report.checks if c.result is not Status.PASS]
    names = set(report.by_name())
    assert {"reports", "report_media", "reward_transactions", "audit_logs_report"} <= names
    assert {
        "ai_classifications",
        "celery_queue_length",
        "throttle_keys",
        "residual_objects",
    } <= names
    assert report.by_name()["users_total"].observed == 0


def test_ready_state_passes_with_exactly_the_ten_actors(ready, env):
    report = st.inspect_ready_state(env)
    assert report.overall is Status.PASS, [c for c in report.checks if c.result is not Status.PASS]
    checks = report.by_name()
    assert (checks["fixture_actors"].observed, checks["users_total"].observed) == (10, 10)
    assert checks["tokens_not_persisted"].result is Status.PASS
    assert checks["throttle_actor_keys"].result is Status.PASS
    # ZERO_STATE no es READY_STATE: con los diez actores, el estado cero falla por usuarios.
    zero = st.inspect_zero_state(env)
    assert result_of(zero, "users_total") is Status.FAIL


def test_required_categories_are_checked(seeded, env):
    Category.objects.filter(slug="semaforos").update(is_active=False)
    report = st.inspect_zero_state(env)
    assert result_of(report, "required_categories_active") is Status.FAIL
    Category.objects.filter(slug="semaforos").delete()
    report = st.inspect_zero_state(env)
    assert result_of(report, "categories_total") is Status.FAIL
    assert report.overall is Status.FAIL


@pytest.mark.parametrize(
    ("residual", "check"),
    [
        ("report", "reports"),
        ("media", "report_media"),
        ("ledger", "reward_transactions"),
        ("audit", "audit_logs_report"),
        ("classification", "ai_classifications"),
    ],
)
def test_residual_dynamic_rows_fail_the_ready_state(ready, env, residual, check):
    report = make_report()
    if residual == "media":
        ReportMedia.objects.create(
            report=report,
            file_url="reports/local-test/photo.png",
            file_hash="0" * 64,
            media_type=MediaType.FOTO,
            uploaded_by=report.citizen,
        )
    elif residual == "ledger":
        ledger.on_report_created(report)
    elif residual == "audit":
        audit.log(report.citizen, "report.created", {"report_id": str(report.id)})
    elif residual == "classification":
        AiClassification.objects.create(report=report, model_name="local-test")
    state = st.inspect_ready_state(env)
    assert result_of(state, check) is Status.FAIL
    assert state.overall is Status.FAIL


def test_audit_of_other_domains_is_not_report_audit(ready, env):
    audit.log(None, "catalog.seeded", {})
    assert result_of(st.inspect_ready_state(env), "audit_logs_report") is Status.PASS


def test_queue_unacked_and_throttle_residue_fail(ready, env, redis):
    redis.put("celery", "list", [b"message"])
    redis.put("unacked", "hash", {"tag": b"message"})
    redis.put(cache.make_key(st.throttle_key("someone-else")), "string", b"[]")
    actor = ex10_fixture.actor_specs()[2].user_id
    cache.set(st.throttle_key(str(actor)), [1.0], 60)
    checks = st.inspect_ready_state(env).by_name()
    for name in ("celery_queue_length", "celery_unacked", "throttle_keys", "throttle_actor_keys"):
        assert checks[name].result is Status.FAIL, name
    assert checks["throttle_keys"].observed == 1


# --- Contrato del broker: Celery 5.4 / Kombu 5.6 ------------------------------------------------


def test_broker_keys_are_the_ones_of_the_installed_kombu_and_celery():
    keys = st.broker_keys()
    assert keys.queues == ("celery", "celery\x06\x163", "celery\x06\x166", "celery\x06\x169")
    assert (keys.unacked, keys.unacked_index) == ("unacked", "unacked_index")
    assert keys.result_patterns == ("celery-task-meta-*", "celery-taskset-meta-*", "chord-unlock-*")
    assert keys.static_bindings == "_kombu.binding.*"
    assert keys.worker_ephemeral == ("unacked_mutex", "*.celery.pidbox", "celeryev.*")
    # Las mismas claves que escribe Kombu al entregar: una lista por paso de prioridad.
    channel = SimpleNamespace(sep=Channel.sep, priority=partial(Channel.priority, Channel))
    assert keys.queues == tuple(
        Channel._q_for_pri(channel, "celery", step) for step in Channel.priority_steps
    )


def test_broker_keys_follow_the_transport_options():
    app = SimpleNamespace(
        conf=SimpleNamespace(
            broker_transport_options={
                "global_keyprefix": "ex10:",
                "sep": ":",
                "priority_steps": [0, 5],
                "unacked_key": "pending",
            },
            task_default_queue="celery",
            control_exchange="celery",
            event_queue_prefix="celeryev",
            result_backend="redis://redis:6379/11",
            broker_url="redis://redis:6379/12",
        )
    )
    keys = st.broker_keys(app)
    assert keys.queues == ("ex10:celery", "ex10:celery:5")
    assert (keys.unacked, keys.unacked_index) == ("ex10:pending", "ex10:unacked_index")
    assert keys.static_bindings == "ex10:_kombu.binding.*"
    assert keys.result_patterns is None  # los resultados viven en otro Redis


@pytest.mark.parametrize(("unacked", "unacked_index"), [(0, 1), (1, 0), (0, 0)])
def test_unacked_and_its_index_are_checked_independently(ready, env, redis, unacked, unacked_index):
    if unacked:
        redis.put("unacked", "hash", {"tag": b"message"})
    if unacked_index:
        redis.put("unacked_index", "zset", {"tag": 1.0})
    state = st.inspect_ready_state(env)
    checks = state.by_name()
    assert (checks["celery_unacked"].observed, checks["celery_unacked_index"].observed) == (
        unacked,
        unacked_index,
    )
    assert state.overall is (Status.FAIL if unacked or unacked_index else Status.PASS)


@pytest.mark.parametrize("key", ["celery\x06\x163", "celery\x06\x169"])
def test_messages_in_a_priority_queue_fail(ready, env, redis, key):
    redis.put(key, "list", [b"message"])
    check = st.inspect_ready_state(env).by_name()["celery_queue_length"]
    assert (check.observed, check.result) == (1, Status.FAIL)


@pytest.mark.parametrize(
    "key", ["celery-task-meta-0f0", "celery-taskset-meta-0f0.j", "chord-unlock-0f0"]
)
def test_residual_task_results_fail(ready, env, redis, key):
    redis.put(key, "string", b"{}")
    state = st.inspect_ready_state(env)
    assert result_of(state, "celery_result_keys") is Status.FAIL
    assert state.overall is Status.FAIL


def test_static_bindings_and_worker_keys_do_not_fail_the_prepared_state(ready, env, redis):
    redis.put("_kombu.binding.celery", "set", {"celery\x06\x16\x06\x16celery"})
    redis.put("_kombu.binding.celery.pidbox", "set", {"\x06\x16\x06\x16w1.celery.pidbox"})
    redis.put("_kombu.binding.reply.celery.pidbox", "set", {"x\x06\x16\x06\x16x"})
    redis.put("unacked_mutex", "string", b"token")
    redis.put("0f0.reply.celery.pidbox", "list", [b"reply"])
    state = st.inspect_ready_state(env)
    assert state.overall is Status.PASS, [c for c in state.checks if c.result is not Status.PASS]


def test_a_result_backend_outside_the_broker_redis_is_incomplete(ready, env, monkeypatch):
    monkeypatch.setattr(st, "_result_patterns", lambda app: None)
    state = st.inspect_ready_state(env)
    check = state.by_name()["celery_result_keys"]
    assert (check.result, check.detail) == (Status.INCOMPLETE, "result-backend-not-inspected")
    assert state.overall is Status.INCOMPLETE


def test_throttle_key_is_the_one_drf_scoped_throttle_uses():
    actor = ex10_fixture.actor_specs()[0].user_id
    throttle = ScopedRateThrottle()
    throttle.scope = "report_create"
    user = SimpleNamespace(is_authenticated=True, pk=actor)
    assert throttle.get_cache_key(SimpleNamespace(user=user), view=None) == st.throttle_key(
        str(actor)
    )
    assert fnmatch.fnmatchcase(
        cache.make_key(st.throttle_key(str(actor))), st.throttle_redis_pattern()
    )


def test_residual_or_linked_objects_fail(ready, env, roots):
    stray = roots.objects / "s11-reportes" / "reports" / "x" / "photo.png"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"\x89PNG")
    (roots.objects / "evidence-link").symlink_to(roots.evidence["runs"], target_is_directory=True)
    checks = st.inspect_ready_state(env).by_name()
    assert (checks["residual_objects"].observed, checks["residual_objects"].result) == (
        1,
        Status.FAIL,
    )
    assert checks["object_symlinks"].result is Status.FAIL


def test_unavailable_redis_is_incomplete_not_fail(ready, env, redis):
    redis.down = True
    state = st.inspect_ready_state(env)
    checks = state.by_name()
    for name in (
        "celery_queue_length",
        "celery_unacked",
        "celery_unacked_index",
        "celery_result_keys",
        "throttle_keys",
    ):
        assert (checks[name].result, checks[name].detail) == (
            Status.INCOMPLETE,
            "redis-unavailable",
        ), name
    assert state.overall is Status.INCOMPLETE


def test_unavailable_database_is_incomplete(ready, env, monkeypatch):
    def unavailable():
        raise OperationalError("could not connect to 10.0.0.1 password=local-test")

    monkeypatch.setattr(Report.objects, "count", unavailable)
    state = st.inspect_ready_state(env)
    check = state.by_name()["reports"]
    assert (check.result, check.detail) == (Status.INCOMPLETE, "database-unavailable")
    assert state.overall is Status.INCOMPLETE
    assert "password" not in json.dumps(state.to_json())


def test_missing_duplicate_and_drifted_actors_fail(ready, env):
    specs = ex10_fixture.actor_specs()
    User.objects.filter(pk=specs[0].user_id).delete()
    checks = st.inspect_ready_state(env).by_name()
    assert checks["fixture_actors"].detail == "missing=1 drift=0"
    ex10_fixture.prepare_fixture()
    User.objects.create_user(email="s11-ex10-extra@example.invalid", password=None)
    checks = st.inspect_ready_state(env).by_name()
    assert (checks["users_total"].observed, checks["users_total"].result) == (11, Status.FAIL)
    User.objects.filter(email="s11-ex10-extra@example.invalid").delete()
    User.objects.filter(pk=specs[4].user_id).update(role=UserRole.ADMIN)
    checks = st.inspect_ready_state(env).by_name()
    assert (checks["fixture_actors"].result, checks["fixture_actors"].detail) == (
        Status.FAIL,
        "missing=0 drift=1",
    )


# --- Reset ------------------------------------------------------------------------------------


@pytest.fixture
def clean_contenttype_cache():
    yield
    # `flush` recrea los content types: nada de la caché debe sobrevivir al test.
    ContentType.objects.clear_cache()


def evidence_hashes(roots):
    return {
        name: hashlib.sha256((path / "SENTINEL").read_bytes()).hexdigest()
        for name, path in roots.evidence.items()
    }


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_reset_clears_everything_dynamic_and_keeps_references(
    env, roots, redis, clean_contenttype_cache
):
    call_command("seed_catalogs", verbosity=0)
    ex10_fixture.prepare_fixture()
    report = make_report()
    ledger.on_report_created(report)
    audit.log(report.citizen, "report.created", {"report_id": str(report.id)})
    redis.put("celery", "list", [b"message"])
    redis.put("unacked", "hash", {"tag": b"message"})
    redis.put("unacked_index", "zset", {"tag": 1.0})
    redis.put("celery-task-meta-0f0", "string", b"{}")
    redis.put(cache.make_key(st.throttle_key("someone")), "string", b"[]")
    stray = roots.objects / "s11-reportes" / "reports" / "photo.png"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"\x89PNG")
    before = evidence_hashes(roots)

    result = st.reset_ex10_state(env)
    zero = st.inspect_zero_state(env)
    assert zero.overall is Status.PASS, [c for c in zero.checks if c.result is not Status.PASS]
    assert (zero.by_name()["categories_total"].observed, redis.flushes) == (10, 1)
    assert (roots.objects / st.OBJECT_ROOT_MARKER).is_file()
    assert evidence_hashes(roots) == before  # la evidencia no se toca
    assert result.to_json()["strategy"] == "total-isolated-flush"

    st.reset_ex10_state(env)  # idempotente
    assert st.inspect_zero_state(env).overall is Status.PASS


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_reset_with_fixture_restores_the_same_actors_and_ready_state(env, clean_contenttype_cache):
    call_command("seed_catalogs", verbosity=0)
    first = ex10_fixture.prepare_fixture()
    make_report()
    result = st.reset_ex10_state(env, restore_fixture=True)
    assert [a.user_id for a in result.fixture.actors] == [a.user_id for a in first.actors]
    assert {a.result for a in result.fixture.actors} == {ex10_fixture.ActorResult.CREATED}
    ready = st.inspect_ready_state(env)
    assert ready.overall is Status.PASS, [c for c in ready.checks if c.result is not Status.PASS]


@pytest.mark.django_db
def test_reset_refuses_symlinks_before_destroying_anything(seeded, env, roots):
    ex10_fixture.prepare_fixture()
    make_report()
    (roots.objects / "evidence-link").symlink_to(roots.evidence["runs"], target_is_directory=True)
    before = evidence_hashes(roots)
    with pytest.raises(st.Ex10StateError, match="STORAGE_SYMLINK"):
        st.reset_ex10_state(env)
    assert Report.objects.count() == 1 and User.objects.count() == 10
    assert evidence_hashes(roots) == before


@pytest.mark.django_db
def test_reset_refuses_unavailable_redis_or_uninitialized_storage(seeded, env, roots, redis):
    ex10_fixture.prepare_fixture()
    make_report()
    redis.down = True
    with pytest.raises(st.Ex10StateError, match="REDIS_UNAVAILABLE"):
        st.reset_ex10_state(env)
    redis.down = False
    (roots.objects / st.OBJECT_ROOT_MARKER).unlink()
    with pytest.raises(st.Ex10StateError, match="STORAGE_NOT_INITIALIZED"):
        st.reset_ex10_state(env)
    assert Report.objects.count() == 1 and redis.flushes == 0


@pytest.mark.django_db
def test_reset_rejects_a_later_loss_of_isolation(seeded, env, settings):
    settings.S11_ENVIRONMENT = "development"
    with pytest.raises(st.Ex10StateError, match="ISOLATION_REJECTED"):
        st.reset_ex10_state(env)


@pytest.mark.django_db
def test_reset_rejects_an_invalid_dataset_before_flushing(seeded, env):
    ex10_fixture.prepare_fixture()
    bad = ex10_load.LoadDataset("0" * 64, ex10_load.load_dataset().payload)
    with pytest.raises(ex10_fixture.Ex10FixtureError):
        st.reset_ex10_state(env, restore_fixture=True, dataset=bad)
    assert User.objects.count() == 10


# --- Seguridad ----------------------------------------------------------------------------------


def test_payloads_logs_and_errors_hold_no_secrets_or_personal_material(
    ready, env, caplog, settings
):
    dataset = ex10_load.load_dataset()
    with caplog.at_level("DEBUG"):
        tokens = ex10_fixture.issue_tokens(ready)
        payloads = [
            ready.to_json(),
            st.inspect_ready_state(env).to_json(),
            st.inspect_zero_state(env).to_json(),
            st.ResetResult(True, ready).to_json(),
        ]
    text = json.dumps(payloads) + caplog.text + repr(env) + repr(ready)
    forbidden = [*tokens.mapping().values(), settings.SECRET_KEY, "@example.invalid", "redis://"]
    forbidden += [u.password for u in User.objects.all()]
    forbidden += [s.document_hmac for s in ex10_fixture.actor_specs()]
    for row in dataset.payload["requests"]:
        forbidden += [repr(row["latitude"]), repr(row["longitude"]), row["image"]["base64"][:40]]
    # Sólo índices: si fallara, el informe de pytest no debe reproducir el valor filtrado.
    leaked = [index for index, value in enumerate(forbidden) if value and value in text]
    assert leaked == []
    assert "iVBORw0KGgo" not in text and base64.b64encode(b"\x89PNG").decode() not in text
    tokens.release()
    assert str(st.Ex10StateError("DATABASE_NOT_APPROVED")) == "DATABASE_NOT_APPROVED"
