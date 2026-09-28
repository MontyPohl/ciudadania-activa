"""EX-10 fase 2B-3: preparación operacional. LOCAL TEST / NON-EXPERIMENTAL.

Base de tests de Django, Redis en memoria inyectado (y el Redis del proyecto Docker de tests
sólo donde se prueba el `FLUSHDB` o los clientes reales) y raíces en `.s11-test-artifacts/`
(`verify_isolation` rechaza `/tmp`). Nunca se usa el stack S11, `var/s11-runs` ni un run: no
hay carga, ni `run_id`, ni POST a `/api/v1/reports/`, ni tareas publicadas.
"""

import dataclasses
import fnmatch
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery.app.task import Task
from celery.signals import before_task_publish
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection, connections
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from rest_framework_simplejwt.tokens import AccessToken

from apps.admin_app.models import AuditLog, Category, Institution
from apps.auth_app.models import CitizenProfile
from apps.core.enums import ReportSource, ReportStatus
from apps.core.services import audit
from apps.experiments_app import ex10_fixture, ex10_load, runtime
from apps.experiments_app import ex10_preflight as pf
from apps.experiments_app import ex10_prepare as pr
from apps.experiments_app import ex10_state as st
from apps.experiments_app.canonical import canonical_json_bytes
from apps.experiments_app.isolation import initialize_object_root
from apps.experiments_app.management.commands import s11_ex10_prepare as command_module
from apps.reports_app.models import Report

User = get_user_model()
R = pf.CheckResult
SHA = "b" * 40
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DECLARED_DB = "ciudadania_s11_unit"
REAL_REDIS = "redis://redis:6379/11"
FOREIGN_REDIS = "redis://redis:6379/12"
CANARY = "canary-prepare-4c1d"


def _redis_reachable() -> bool:
    try:
        return bool(Redis.from_url(REAL_REDIS, socket_connect_timeout=1, socket_timeout=1).ping())
    except (RedisError, OSError):
        return False


needs_redis = pytest.mark.skipif(
    not _redis_reachable(), reason="Requiere el Redis aislado del proyecto Docker de tests."
)


# --- Dobles ------------------------------------------------------------------------------------


class FakeRedis:
    """Un DB lógico de Redis en memoria: clave → (tipo, valor, pttl). Una instancia por test."""

    def __init__(self):
        self.data = {}
        self.down = False
        self.flushes = 0

    def put(self, name, kind, value, pttl=-1):
        self.data[name] = (kind, value, pttl)

    def _up(self):
        if self.down:
            raise RedisConnectionError("fake redis down password=local-test")

    def _key(self, name):
        return name.decode() if isinstance(name, bytes) else name

    def _sized(self, name, kind):
        self._up()
        stored = self.data.get(self._key(name))
        return 0 if stored is None or stored[0] != kind else len(stored[1])

    def ping(self):
        self._up()
        return True

    def llen(self, name):
        return self._sized(name, "list")

    def hlen(self, name):
        return self._sized(name, "hash")

    def zcard(self, name):
        return self._sized(name, "zset")

    def scan_iter(self, match=None, count=None):
        self._up()
        keys = sorted(k for k in self.data if fnmatch.fnmatchcase(k, match or "*"))
        return iter([k.encode() for k in keys])

    def type(self, name):
        self._up()
        stored = self.data.get(self._key(name))
        return (stored[0] if stored else "none").encode()

    def pttl(self, name):
        self._up()
        stored = self.data.get(self._key(name))
        return stored[2] if stored else -2

    def lrange(self, name, start, end):
        self._up()
        return list(self.data[self._key(name)][1])[start : end + 1]

    def flushdb(self):
        self._up()
        self.data.clear()
        self.flushes += 1


class Repo:
    """Checkout completo y limpio en `SHA`; lee el código en ejecución como el checkout."""

    def __init__(self):
        self.source = pf.SourceCompleteness(
            git_dir="directory",
            toplevel=True,
            shallow=False,
            sparse=False,
            hidden_index_entries=0,
            local_excludes=False,
            missing_top_level=0,
            missing_required=0,
        )
        self.head_sha, self.dirty, self.ancestors = SHA, 0, True
        self.files, self.untracked_paths = {}, set()

    def completeness(self):
        return self.source

    def head(self):
        return self.head_sha

    def dirty_entries(self):
        return self.dirty

    def is_ancestor(self, sha):
        return self.ancestors

    def untracked(self, paths):
        return sum(1 for path in paths if path in self.untracked_paths)

    def read(self, relative):
        if relative in self.files:
            return self.files[relative]
        return (Path(django_settings.BASE_DIR) / relative.removeprefix("backend/")).read_bytes()


class Resolver:
    """DNS de Docker simulado: sólo resuelve lo que está «vivo»."""

    def __init__(self, alive=("redis",)):
        self.alive = set(alive)
        self.calls = []

    def __call__(self, name):
        self.calls.append(name)
        return name in self.alive


@pytest.fixture
def roots(settings, request):
    base = Path.cwd() / ".s11-test-artifacts" / f"ex10-prepare-{request.node.name}"
    shutil.rmtree(base, ignore_errors=True)
    evidence = {name: base / "evidence" / name for name in ("runs", "backup", "restore")}
    for path in evidence.values():
        path.mkdir(parents=True)
        (path / "SENTINEL").write_bytes(b"historical-evidence")
    source = base / "source"
    source.mkdir()
    settings.S11_EXPERIMENTS_ENABLED = True
    settings.S11_ENVIRONMENT = "isolated-experiment"
    settings.S11_DATABASE_URL = f"postgis://ciudadania@db:5432/{DECLARED_DB}"
    settings.REDIS_URL = REAL_REDIS
    settings.S11_OBJECT_ROOT = base / "objects"
    settings.S11_ARTIFACT_ROOT = evidence["runs"]
    settings.S11_BACKUP_ROOT = evidence["backup"]
    settings.S11_RESTORE_ROOT = evidence["restore"]
    initialize_object_root(runtime.isolation_config())
    yield SimpleNamespace(base=base, objects=base / "objects", evidence=evidence, source=source)
    # Sin `ignore_errors`: una raíz que no se pudiera borrar quedaría para el test siguiente.
    shutil.rmtree(base)


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def env(roots, redis, monkeypatch):
    # Simula estar conectado a la base aislada declarada: la guarda real se prueba aparte.
    monkeypatch.setattr(st, "_connected_database_name", lambda: DECLARED_DB)
    cache.clear()
    yield st.approve_environment(redis_factory=lambda url: redis)
    cache.clear()


@pytest.fixture
def stack(env, roots, redis):
    repo, resolver = Repo(), Resolver()
    clients = {"same_db": 0, "other_clients": 0}
    preflight = pf.Probes(
        repository=lambda root: repo,
        approve_environment=lambda: st.approve_environment(redis_factory=lambda url: redis),
        database_identity=lambda: {"database": True, "user": True, "host": True, "port": True},
        settings_module=lambda: "config.settings.test",
    )
    probes = pr.PrepareProbes(
        preflight=preflight,
        resolve=resolver,
        redis_clients=lambda url: dict(clients),
        pending_migrations=lambda: 0,
        readonly_environment=lambda: st.approve_environment(redis_factory=lambda url: redis),
        writable_environment=lambda: st.approve_environment(redis_factory=lambda url: redis),
        clock=lambda: NOW,
    )
    return SimpleNamespace(
        probes=probes,
        repo=repo,
        resolver=resolver,
        clients=clients,
        redis=redis,
        roots=roots,
        options=pr.PrepareOptions(SHA, roots.source),
    )


def prepare(stack, mode=pr.Mode.PREPARE):
    options = pr.PrepareOptions(SHA, stack.roots.source, mode)
    return pr.run_prepare(options, stack.probes)


def checks(report):
    return {check.check: check for check in report.checks}


def not_pass(report):
    return [
        (c.check, str(c.result), c.detail_code) for c in report.checks if c.result is not R.PASS
    ]


@pytest.fixture
def clean_contenttype_cache():
    yield
    # `flush` recrea los content types: nada de la caché debe sobrevivir al test.
    ContentType.objects.clear_cache()


transactional = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def seed_dirty_state(stack):
    """Estado previo «desconocido»: catálogo, actores, un reporte y residuos en todas partes."""
    call_command("seed_catalogs", stdout=io.StringIO())
    ex10_fixture.prepare_fixture()
    user = User.objects.get(pk=ex10_fixture.actor_specs()[0].user_id)
    category = Category.objects.get(slug="baches")
    report = Report.objects.create(
        citizen=user,
        category=category,
        description="LOCAL-TEST",
        status=ReportStatus.CREADO,
        priority=category.default_priority,
        source=ReportSource.PLATFORM,
        location=Point(-57.5, -25.3, srid=4326),
    )
    audit.log(user, "report.created", {"report_id": str(report.id)})
    User.objects.create_user(email="stranger@example.invalid", password=None)
    stack.redis.put("celery", "list", [b"message"])
    stack.redis.put("unacked", "hash", {"tag": b"message"})
    stack.redis.put("celery-task-meta-0f0", "string", b"{}")
    cache.set(st.throttle_key(str(user.pk)), [1.0], 60)
    stray = stack.roots.objects / "s11-reportes" / "reports" / "x" / "photo.png"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"\x89PNG")
    return report


def evidence_state(roots):
    state = {}
    for name, path in roots.evidence.items():
        for item in sorted(path.rglob("*")):
            info = item.lstat()
            content = item.read_bytes() if item.is_file() else b""
            state[f"{name}/{item.relative_to(path)}"] = (
                hashlib.sha256(content).hexdigest(),
                info.st_mode,
                info.st_mtime_ns,
            )
    return state


# --- Preparación completa ----------------------------------------------------------------------


@transactional
def test_an_isolated_environment_is_prepared_to_the_ready_state(stack, clean_contenttype_cache):
    report_row = seed_dirty_state(stack)
    before = evidence_state(stack.roots)
    report = prepare(stack)
    assert report.result is R.PASS, not_pass(report)
    assert (report.state, report.exit_code) == (pr.PrepareState.READY_STATE, 0)
    payload = report.to_json()
    for flag in ("database_reset", "catalogs_seeded", "object_root_reset"):
        assert payload[flag] is True, flag
    assert (payload["redis_reset"], payload["cache_reset"]) == (True, True)
    assert payload["fixture_result"] == {
        "schema_version": ex10_fixture.FIXTURE_SCHEMA_VERSION,
        "outcome": "PREPARED",
        "count": 10,
        "created": 10,
        "already-matched": 0,
        "drift-detected": 0,
        "missing": 0,
    }
    assert payload["ready_state"]["overall"] == "PASS"
    assert payload["ready_state"]["state"] == "READY_STATE"
    # Flush: el reporte, su auditoría y la cuenta ajena desaparecen; el catálogo vuelve.
    assert not Report.objects.filter(pk=report_row.pk).exists()
    assert not AuditLog.objects.filter(action="report.created").exists()
    assert not User.objects.filter(email="stranger@example.invalid").exists()
    assert (Category.objects.count(), Institution.objects.count()) == (10, 3)
    assert (User.objects.count(), CitizenProfile.objects.count()) == (10, 10)
    # Objetos, Redis 11 y caché vacíos; el marcador se conserva.
    assert [p.name for p in stack.roots.objects.iterdir()] == [st.OBJECT_ROOT_MARKER]
    assert (stack.redis.data, stack.redis.flushes) == ({}, 1)
    for spec in ex10_fixture.actor_specs():
        assert cache.get(st.throttle_key(str(spec.user_id))) is None
    # La evidencia no se toca y la preparación no la declara.
    assert evidence_state(stack.roots) == before
    assert (payload["run_id"], payload["evidence_written"]) == (None, False)
    assert (payload["report_posts"], payload["provider_calls"], payload["task_publishes"]) == (
        0,
        0,
        0,
    )


@transactional
def test_the_actors_keep_the_dataset_identities(stack, clean_contenttype_cache):
    report = prepare(stack)
    assert report.result is R.PASS, not_pass(report)
    users = ex10_load.load_dataset().payload["users"]
    assert [a["user_id"] for a in report.to_json()["actors"]] == [u["id"] for u in users]
    assert [a["alias"] for a in report.to_json()["actors"]] == [u["alias"] for u in users]
    stored = sorted(str(pk) for pk in User.objects.values_list("pk", flat=True))
    assert stored == sorted(u["id"] for u in users)


@transactional
def test_a_second_preparation_is_idempotent(stack, clean_contenttype_cache):
    first = prepare(stack)
    second = prepare(stack)
    assert first.result is second.result is R.PASS, not_pass(second)
    # Mismas identidades, mismo READY_STATE y el mismo JSON byte a byte (reloj fijo).
    assert first.to_bytes() == second.to_bytes()
    assert {a["result"] for a in second.to_json()["actors"]} == {"CREATED"}
    assert stack.redis.flushes == 2


# --- Guardas previas: nada se muta -------------------------------------------------------------


def untouched(stack, row):
    return (
        Report.objects.filter(pk=row.pk).exists()
        and stack.redis.flushes == 0
        and stack.redis.data != {}
        and (stack.roots.objects / "s11-reportes").exists()
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("S11_EXPERIMENTS_ENABLED", False),
        ("S11_ENVIRONMENT", "development"),
        ("S11_DATABASE_URL", "postgis://ciudadania@db:5432/ciudadania_activa"),
        ("S11_DATABASE_URL", "postgis://u@db.supabase.co:5432/ciudadania_s11_x"),
        ("REDIS_URL", "redis://redis:6379/0"),
        ("REDIS_URL", "redis://cache.heroku.example:6379/11"),
    ],
    ids=["disabled", "environment", "db-name", "db-production", "redis-db", "redis-production"],
)
@pytest.mark.django_db
def test_a_non_isolated_environment_is_rejected_before_any_write(stack, settings, name, value):
    row = seed_dirty_state(stack)
    setattr(settings, name, value)
    report = prepare(stack)
    assert report.result is R.FAIL, not_pass(report)
    assert checks(report)["isolation_guards"].detail_code == "isolation-rejected"
    assert untouched(stack, row)
    assert (report.reset, report.ready_state) == (None, None)
    text = report.to_bytes().decode()
    assert "supabase" not in text and "heroku" not in text


@pytest.mark.django_db
def test_the_really_connected_database_must_be_the_declared_one(stack):
    row = seed_dirty_state(stack)
    stack.probes.preflight.database_identity = pf.real_database_identity
    report = prepare(stack)
    check = checks(report)["isolation_database_connected"]
    assert (check.result, check.observed_sanitized["database"]) == (R.FAIL, False)
    assert report.result is R.FAIL and untouched(stack, row)
    # Y sin la simulación del nombre conectado, la aprobación misma lo rechaza.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(st, "_connected_database_name", lambda: connection.settings_dict["NAME"])
        report = prepare(stack)
    assert checks(report)["isolation_guards"].detail_code == "database-not-approved"
    assert untouched(stack, row)


@pytest.mark.django_db
def test_a_redis_db_other_than_11_is_rejected(stack, settings):
    row = seed_dirty_state(stack)
    settings.REDIS_URL = "redis://redis:6379/12"
    report = prepare(stack)
    assert report.result is R.FAIL and untouched(stack, row)
    assert checks(report)["isolation_guards"].detail_code == "isolation-rejected"


@pytest.mark.django_db
def test_invalid_storage_is_rejected(stack, settings, roots, tmp_path):
    row = seed_dirty_state(stack)
    settings.S11_OBJECT_ROOT = tmp_path / "objects"  # /tmp: verify_isolation lo rechaza
    report = prepare(stack)
    assert checks(report)["isolation_guards"].detail_code == "isolation-rejected"
    settings.S11_OBJECT_ROOT = roots.objects
    (roots.objects / st.OBJECT_ROOT_MARKER).unlink()
    report = prepare(stack)
    assert report.result is R.FAIL, not_pass(report)
    assert checks(report)["isolation_storage_roots"].detail_code == "object-root-marker"
    assert checks(report)["storage_contents"].result is R.NOT_RUN
    assert untouched(stack, row)


@pytest.mark.django_db
def test_storage_overlapping_evidence_is_rejected(stack, settings, roots):
    row = seed_dirty_state(stack)
    settings.S11_OBJECT_ROOT = roots.evidence["runs"] / "objects"
    report = prepare(stack)
    assert checks(report)["isolation_guards"].detail_code == "storage-overlaps-evidence"
    assert report.result is R.FAIL and untouched(stack, row)


@pytest.mark.django_db
def test_symlinked_roots_are_rejected(stack, settings, roots):
    row = seed_dirty_state(stack)
    (roots.objects / "evidence-link").symlink_to(roots.evidence["runs"], target_is_directory=True)
    report = prepare(stack)
    assert checks(report)["storage_contents"].detail_code == "storage-symlink"
    assert report.result is R.FAIL and untouched(stack, row)
    (roots.objects / "evidence-link").unlink()
    link = roots.base / "linked-objects"
    link.symlink_to(roots.objects, target_is_directory=True)
    settings.S11_OBJECT_ROOT = link
    report = prepare(stack)
    assert checks(report)["isolation_guards"].detail_code == "storage-symlink"
    settings.S11_OBJECT_ROOT = roots.objects
    backup = roots.evidence["backup"]
    moved = roots.base / "real-backup"
    backup.rename(moved)
    backup.symlink_to(moved, target_is_directory=True)
    report = prepare(stack)
    assert checks(report)["isolation_storage_roots"].detail_code == "storage-symlink"
    assert report.result is R.FAIL and untouched(stack, row)
    backup.unlink()
    moved.rename(backup)


@pytest.mark.parametrize("service", ["backend", "worker", "beat"])
@pytest.mark.django_db
def test_a_live_ex10_service_blocks_the_reset(stack, service):
    row = seed_dirty_state(stack)
    stack.resolver.alive.add(service)
    report = prepare(stack)
    check = checks(report)["ex10_services_stopped"]
    assert (check.result, check.detail_code) == (R.FAIL, "ex10-services-active")
    assert check.observed_sanitized[f"{service}_resolves"] is True
    assert report.result is R.FAIL and untouched(stack, row)


@pytest.mark.django_db
def test_a_client_of_the_approved_redis_blocks_the_reset(stack):
    row = seed_dirty_state(stack)
    stack.clients["same_db"] = 1
    report = prepare(stack)
    check = checks(report)["ex10_services_stopped"]
    assert (check.result, check.observed_sanitized["redis_clients_same_db"]) == (R.FAIL, 1)
    assert report.result is R.FAIL and untouched(stack, row)


@pytest.mark.django_db
def test_services_that_cannot_be_shown_stopped_are_incomplete(stack):
    row = seed_dirty_state(stack)
    stack.resolver.alive.discard("redis")  # el nombre de control no resuelve
    report = prepare(stack)
    check = checks(report)["ex10_services_stopped"]
    assert (check.result, check.detail_code) == (R.INCOMPLETE, "ex10-services-not-demonstrated")
    assert report.result is R.INCOMPLETE and untouched(stack, row)
    stack.resolver.alive.add("redis")
    stack.probes.resolve = lambda name: None if name == "worker" else name == "redis"
    assert prepare(stack).result is R.INCOMPLETE and untouched(stack, row)


@pytest.mark.django_db
def test_a_service_that_starts_after_the_guards_blocks_the_reset(stack):
    row = seed_dirty_state(stack)
    real = stack.probes.writable_environment

    def worker_starts_now():
        stack.resolver.alive.add("worker")  # entre las guardas y el primer paso destructivo
        return real()

    stack.probes.writable_environment = worker_starts_now
    report = prepare(stack)
    assert (report.reset.result, report.reset.detail_code) == (R.FAIL, "ex10-services-active")
    assert report.to_json()["database_reset"] is False
    assert report.result is R.FAIL and untouched(stack, row)


@transactional
def test_a_service_that_starts_during_the_reset_fails_the_preparation(
    stack, clean_contenttype_cache
):
    real_reset = stack.probes.reset

    def reset_then_worker_starts(*args, **kwargs):
        result = real_reset(*args, **kwargs)
        stack.resolver.alive.add("worker")
        return result

    stack.probes.reset = reset_then_worker_starts
    report = prepare(stack)
    assert (report.reset.result, report.reset.detail_code) == (R.FAIL, "ex10-services-started")
    assert report.result is R.FAIL and report.state is pr.PrepareState.NOT_READY


@pytest.mark.django_db
def test_an_unavailable_redis_is_incomplete_and_nothing_is_written(stack):
    row = seed_dirty_state(stack)
    stack.redis.down = True
    report = prepare(stack)
    assert checks(report)["isolation_redis"].result is R.INCOMPLETE
    assert checks(report)["ex10_services_stopped"].detail_code == "blocked-by:isolation_redis"
    assert (report.result, report.state, report.exit_code) == (
        R.INCOMPLETE,
        pr.PrepareState.INCOMPLETE,
        2,
    )
    stack.redis.down = False
    assert untouched(stack, row)
    assert "password" not in report.to_bytes().decode()


@pytest.mark.django_db
def test_an_unavailable_database_is_incomplete_and_nothing_is_written(stack):
    row = seed_dirty_state(stack)

    def unavailable():
        raise OperationalError(f"could not connect to 10.0.0.1 password={CANARY}")

    stack.probes.preflight.database_identity = unavailable
    report = prepare(stack)
    assert checks(report)["isolation_database_connected"].result is R.INCOMPLETE
    assert checks(report)["database_schema_current"].result is R.NOT_RUN
    assert report.result is R.INCOMPLETE and untouched(stack, row)
    stack.probes.preflight.database_identity = lambda: dict.fromkeys(
        ("database", "user", "host", "port"), True
    )
    stack.probes.pending_migrations = unavailable
    report = prepare(stack)
    assert checks(report)["database_schema_current"].detail_code == "database-unavailable"
    assert report.result is R.INCOMPLETE and untouched(stack, row)
    assert CANARY not in report.to_bytes().decode()


@transactional
def test_the_first_database_connection_is_allowed_under_the_guard(stack, clean_contenttype_cache):
    # En un proceso nuevo la primera conexión ocurre dentro de la guarda, y psycopg 3.2
    # resuelve el host con `socket.getaddrinfo` desde Python: el host declarado se permite.
    stack.probes.preflight.database_identity = pf.real_database_identity
    stack.probes.pending_migrations = pr.real_pending_migrations
    connection.close()
    report = prepare(stack)
    names = checks(report)
    assert not any(name.endswith("_aborted") for name in names), not_pass(report)
    assert names["guard_no_side_effects"].result is R.PASS
    # Conecta de verdad: la base de tests no es la declarada, y eso lo dice el check.
    check = names["isolation_database_connected"]
    assert (check.result, check.observed_sanitized["host"]) == (R.FAIL, True)


@transactional
def test_a_database_host_other_than_the_declared_one_is_blocked(
    stack, settings, clean_contenttype_cache
):
    settings.S11_DATABASE_URL = f"postgis://ciudadania@localhost:5432/{DECLARED_DB}"
    stack.probes.preflight.database_identity = pf.real_database_identity
    connection.close()
    report = prepare(stack)
    names = checks(report)
    assert (names["isolation_aborted"].result, names["isolation_aborted"].detail_code) == (
        R.FAIL,
        "guard-network",
    )
    assert names["guard_no_side_effects"].result is R.FAIL
    assert report.result is R.FAIL and report.reset is None


@pytest.mark.django_db
def test_a_database_without_migrations_is_rejected_not_migrated(stack):
    row = seed_dirty_state(stack)
    stack.probes.pending_migrations = lambda: 3
    report = prepare(stack)
    check = checks(report)["database_schema_current"]
    assert (check.result, check.observed_sanitized) == (R.FAIL, {"pending_migrations": 3})
    assert untouched(stack, row)


@pytest.mark.parametrize(
    "url",
    [
        "redis://redis:6379/11?db=0",
        "redis://:secret@redis:6379/11",
        "redis://redis:6380/11",
        "rediss://redis:6379/11",
    ],
    ids=["query-db", "credentials", "port", "scheme"],
)
@pytest.mark.django_db
def test_only_the_exact_protocol_redis_url_is_flushed(stack, settings, url):
    row = seed_dirty_state(stack)
    settings.REDIS_URL = url
    report = prepare(stack)
    names = checks(report)
    # O lo rechaza ya el aislamiento, o la URL exacta del protocolo: nunca llega al reset.
    assert (
        names["isolation_guards"].result is R.FAIL
        or names["redis_endpoint_exact"].detail_code == "redis-url-not-protocol"
    )
    assert report.result is R.FAIL and report.reset is None and untouched(stack, row)
    assert "secret" not in report.to_bytes().decode()


@pytest.mark.django_db
def test_the_real_schema_check_reads_the_applied_migrations():
    assert pr.real_pending_migrations() == 0


@pytest.mark.django_db
def test_the_cache_must_be_the_approved_redis(stack, monkeypatch):
    row = seed_dirty_state(stack)
    # Sin `setting_changed`: la caché de la suite sigue siendo LocMem, sólo cambia lo leído.
    caches = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://redis:6379/0",
        }
    }
    monkeypatch.setattr(django_settings, "CACHES", caches)
    report = prepare(stack)
    check = checks(report)["cache_approved"]
    assert (check.result, check.observed_sanitized["backend"]) == (R.FAIL, "redis")
    assert untouched(stack, row)
    caches["default"]["LOCATION"] = REAL_REDIS
    stack.resolver.alive.add("beat")  # que no llegue al reset: sólo interesa la guarda
    assert checks(prepare(stack))["cache_approved"].result is R.PASS
    caches["default"]["BACKEND"] = "django.core.cache.backends.filebased.FileBasedCache"
    assert checks(prepare(stack))["cache_approved"].detail_code == "cache-not-approved"
    assert untouched(stack, row)


@pytest.mark.parametrize(
    ("layout", "detail"),
    [
        ("open-ex10-run", "ex10-run-open"),
        ("open-transaction", "evidence-transaction-open"),
        ("finalized-ex10-run", None),
        ("other-scenario-open", None),
    ],
)
@pytest.mark.django_db
def test_an_open_ex10_run_or_custody_transaction_blocks_the_reset(stack, roots, layout, detail):
    row = seed_dirty_state(stack)
    runs = roots.evidence["runs"] / "runs"
    run = runs / "s11-00000000-0000-5000-8000-0000000000e1"
    if layout == "open-transaction":
        (roots.evidence["runs"] / "custody-transactions" / "tx-1").mkdir(parents=True)
    else:
        scenario = "EX-09" if layout == "other-scenario-open" else "EX-10"
        (run / "metadata" / scenario).mkdir(parents=True)
        if layout == "finalized-ex10-run":
            (run / ".s11-finalized").write_text("sealed\n")
    before = evidence_state(roots)
    stack.resolver.alive.add("beat")  # que ninguna variante llegue al reset
    report = prepare(stack)
    check = checks(report)["evidence_idle"]
    assert check.detail_code == detail
    assert check.result is (R.FAIL if detail else R.PASS)
    assert untouched(stack, row)
    assert evidence_state(roots) == before


@pytest.mark.django_db
def test_prepare_code_must_match_the_checkout(stack):
    row = seed_dirty_state(stack)
    stack.repo.files[pr.PREPARE_SOURCES[0]] = b"# otro codigo\n"
    report = prepare(stack)
    check = checks(report)["prepare_running_source"]
    assert (check.result, check.observed_sanitized["mismatched"]) == (R.FAIL, 1)
    stack.repo.files.clear()
    stack.repo.untracked_paths.add(pr.PREPARE_SOURCES[1])
    check = checks(prepare(stack))["prepare_running_source"]
    assert (check.result, check.observed_sanitized["untracked"]) == (R.FAIL, 1)
    assert untouched(stack, row)


@pytest.mark.parametrize(
    ("breakage", "check", "result"),
    [
        (lambda s: setattr(s.repo, "head_sha", "c" * 40), "repository_head", R.FAIL),
        (lambda s: setattr(s.repo, "dirty", 2), "repository_clean", R.FAIL),
        (lambda s: setattr(s.repo, "ancestors", False), "protocol_decision_commits", R.FAIL),
        (
            lambda s: setattr(s.repo, "source", dataclasses.replace(s.repo.source, sparse=True)),
            "repository_source_complete",
            R.INCOMPLETE,
        ),
    ],
    ids=["head", "dirty", "g0", "partial"],
)
@pytest.mark.django_db
def test_the_checkout_must_be_the_expected_complete_clean_one(stack, breakage, check, result):
    row = seed_dirty_state(stack)
    breakage(stack)
    report = prepare(stack)
    assert checks(report)[check].result is result
    assert report.result is result and untouched(stack, row)


@pytest.mark.django_db
def test_an_altered_dataset_is_rejected_before_any_write(stack):
    row = seed_dirty_state(stack)
    real = ex10_load.load_dataset()
    stack.probes.preflight.load_dataset = lambda: ex10_load.LoadDataset("0" * 64, real.payload)
    report = prepare(stack)
    assert checks(report)["dataset_sha256"].result is R.FAIL
    assert checks(report)["plan_actors"].result is R.NOT_RUN
    assert report.result is R.FAIL and untouched(stack, row)


@pytest.mark.django_db
def test_a_side_effect_during_the_guards_is_blocked_and_fails(stack, roots):
    row = seed_dirty_state(stack)

    def writes_while_checking():
        (roots.evidence["runs"] / "intruder.txt").write_text("x")
        return 0

    stack.probes.pending_migrations = writes_while_checking
    report = prepare(stack)
    assert checks(report)["database_schema_current"].detail_code == "guard-filesystem-write"
    assert checks(report)["guard_no_side_effects"].result is R.FAIL
    assert not (roots.evidence["runs"] / "intruder.txt").exists()
    assert report.result is R.FAIL and untouched(stack, row)


# --- Fallos durante el reset --------------------------------------------------------------------


@transactional
def test_a_fixture_failure_fails_with_a_sanitized_code(stack, monkeypatch, clean_contenttype_cache):
    def invalid(dataset=None, **kwargs):
        issue = ex10_fixture.DatasetIssue("alias", "canonical-alias-required", 3)
        raise ex10_fixture.Ex10FixtureError("DATASET_INVALID", (issue,))

    monkeypatch.setattr(ex10_fixture, "prepare_fixture", invalid)
    report = prepare(stack)
    assert (report.reset.result, report.reset.detail_code) == (R.FAIL, "fixture-dataset-invalid")
    assert (report.result, report.state, report.exit_code) == (
        R.FAIL,
        pr.PrepareState.NOT_READY,
        1,
    )
    payload = report.to_json()
    # El reset quedó a medias: qué pasos se aplicaron no se afirma.
    assert payload["database_reset"] is None and payload["redis_reset"] is None
    assert payload["fixture_result"] is None and payload["actors"] is None
    # El estado resultante se informa: sin actores no hay READY_STATE.
    assert payload["ready_state"]["overall"] == "FAIL"
    assert "canonical-alias-required" not in report.to_bytes().decode()


@transactional
def test_a_fixture_with_drift_fails(stack, monkeypatch, clean_contenttype_cache):
    real = ex10_fixture.prepare_fixture

    def drifted(dataset=None, **kwargs):
        result = real(dataset, **kwargs)
        return ex10_fixture.FixtureResult(
            ex10_fixture.FixtureOutcome.DRIFT_DETECTED, result.actors, None
        )

    monkeypatch.setattr(ex10_fixture, "prepare_fixture", drifted)
    report = prepare(stack)
    assert report.reset.detail_code == "fixture-drift-detected"
    assert report.result is R.FAIL


@transactional
def test_redis_lost_during_the_reset_is_incomplete(stack, clean_contenttype_cache):
    real_reset = stack.probes.reset

    def redis_goes_down(environment, **kwargs):
        stack.redis.down = True
        return real_reset(environment, **kwargs)

    stack.probes.reset = redis_goes_down
    report = prepare(stack)
    assert report.reset.result is R.INCOMPLETE
    assert report.reset.detail_code == "reset-redis-unavailable"
    assert report.result is R.INCOMPLETE and report.exit_code == 2
    stack.redis.down = False


@transactional
def test_the_database_lost_during_the_reset_is_incomplete(stack, clean_contenttype_cache):
    def interrupted(environment, **kwargs):
        raise OperationalError(f"server closed the connection password={CANARY}")

    stack.probes.reset = interrupted
    report = prepare(stack)
    assert (report.reset.result, report.reset.detail_code) == (R.INCOMPLETE, "reset-interrupted")
    assert report.result is R.INCOMPLETE
    assert CANARY not in report.to_bytes().decode()


@transactional
def test_a_flush_error_and_a_dead_lock_session_are_incomplete_with_json(
    stack, clean_contenttype_cache
):
    def flush_fails(environment, **kwargs):
        raise CommandError("Database test couldn't be flushed. password=" + CANARY)

    stack.probes.reset = flush_fails
    real_cursor = connection.cursor

    class DeadUnlock:
        """El `pg_advisory_unlock` falla: la sesión que tenía el lock ya no existe."""

        def __init__(self):
            self.calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise OperationalError("server closed the connection unexpectedly")
            return real_cursor(*args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(connection, "cursor", DeadUnlock())
        report = prepare(stack)
    assert (report.reset.result, report.reset.detail_code) == (R.INCOMPLETE, "reset-interrupted")
    assert report.result is R.INCOMPLETE and CANARY not in report.to_bytes().decode()


@pytest.mark.parametrize("publisher", ["apply_async", "delay", "send_task", "signal"])
@transactional
def test_a_task_published_during_the_reset_is_blocked(stack, publisher, clean_contenttype_cache):
    from apps.ai_client_app.tasks import classify_report
    from config import celery_app

    real_reset = stack.probes.reset
    swallowed = []

    def publishes(environment, **kwargs):
        result = real_reset(environment, **kwargs)
        try:
            if publisher == "apply_async":
                classify_report.apply_async(args=["00000000-0000-0000-0000-000000000000"])
            elif publisher == "delay":
                classify_report.delay("00000000-0000-0000-0000-000000000000")
            elif publisher == "send_task":
                celery_app.send_task(ex10_load.TASK_NAME, args=["x"])
            else:
                before_task_publish.send(sender=ex10_load.TASK_NAME, body={}, headers={})
        except Exception as exc:  # noqa: BLE001 - como un `on_commit` robusto
            swallowed.append(type(exc).__name__)
        return result

    stack.probes.reset = publishes
    report = prepare(stack)
    assert (report.reset.result, report.reset.detail_code) == (R.FAIL, "task-publish-attempted")
    assert report.result is R.FAIL
    # Tragada la excepción, el intento igual queda anotado; en eager la tarea nunca corrió.
    assert swallowed == ([] if publisher == "signal" else ["_PublishAttempted"])
    assert not Report.objects.exists()
    # Fuera del reset todo vuelve a su sitio: el bloqueo no se filtra al resto del proceso.
    assert Task.__dict__["apply_async"].__name__ == "apply_async"
    assert type(celery_app).__dict__["send_task"].__name__ == "send_task"


@transactional
def test_a_concurrent_preparation_holding_the_lock_is_rejected(stack, clean_contenttype_cache):
    holder = connections.create_connection("default")
    try:
        with holder.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [pr._LOCK_KEY])
        report = prepare(stack)
        assert (report.reset.result, report.reset.detail_code) == (
            R.FAIL,
            "prepare-already-running",
        )
        assert report.to_json()["database_reset"] is False
    finally:
        holder.close()
    assert prepare(stack).result is R.PASS


def test_a_second_preparation_in_the_same_process_is_rejected(stack):
    started, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        started.set()
        release.wait(10)
        return 0

    stack.probes.pending_migrations = slow
    stack.resolver.alive.add("beat")  # el primero termina en las guardas, sin reset
    errors = []

    def first():
        try:
            prepare(stack)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert started.wait(10)
        with pytest.raises(pr.PrepareBusy):
            prepare(stack)
    finally:
        release.set()
        thread.join(10)
    assert errors == []


# --- INSPECT: sólo lectura ----------------------------------------------------------------------


@pytest.mark.django_db
def test_inspect_reads_without_writing_and_reports_the_state(stack, roots):
    row = seed_dirty_state(stack)
    stack.resolver.alive.update({"backend", "worker"})  # vivos: en INSPECT sólo se informa
    report = prepare(stack, pr.Mode.INSPECT)
    assert report.result is R.FAIL and report.reset is None
    assert "ex10_services_stopped" not in checks(report)
    assert report.to_json()["services"]["backend_resolves"] is True
    assert report.to_json()["database_reset"] is False
    assert checks(report)["ready_state"].result is R.FAIL
    assert untouched(stack, row)


@transactional
def test_inspect_after_a_preparation_passes(stack, clean_contenttype_cache):
    assert prepare(stack).result is R.PASS
    stack.resolver.alive.update({"backend", "worker"})
    report = prepare(stack, pr.Mode.INSPECT)
    assert report.result is R.PASS, not_pass(report)
    assert report.state is pr.PrepareState.READY_STATE
    assert stack.redis.flushes == 1  # sólo la preparación
    payload = report.to_json()
    assert payload["fixture_result"] is None and payload["actors"] is None


@transactional
def test_a_bound_worker_control_plane_does_not_fail_the_inspection(stack, clean_contenttype_cache):
    assert prepare(stack).result is R.PASS
    stack.redis.put("_kombu.binding.celery", "set", {"celery"})
    stack.redis.put("_kombu.binding.celery.pidbox", "set", {"x"})
    stack.redis.put("unacked_mutex", "string", b"token", pttl=5000)
    assert prepare(stack, pr.Mode.INSPECT).result is R.PASS
    stack.redis.put("stranger-key", "string", b"?")
    report = prepare(stack, pr.Mode.INSPECT)
    assert (checks(report)["broker_state"].result, report.result) == (R.INCOMPLETE, R.INCOMPLETE)
    stack.redis.put("celery", "list", [b"message"])
    assert checks(prepare(stack, pr.Mode.INSPECT))["broker_state"].result is R.FAIL


# --- Invariantes y NOT_RUN ----------------------------------------------------------------------


def test_not_run_needs_a_declared_blocking_cause():
    blocked = pf.Check("storage_contents", R.NOT_RUN, detail_code="blocked-by:isolation_guards")
    guard_fail = pf.Check("isolation_guards", R.FAIL)
    assert pr.aggregate((guard_fail, blocked)) is R.FAIL
    assert pr.aggregate((pf.Check("isolation_guards", R.PASS), blocked)) is R.INCOMPLETE
    unrelated = pf.Check("storage_contents", R.NOT_RUN, detail_code="blocked-by:dataset_sha256")
    assert pr.aggregate((pf.Check("dataset_sha256", R.FAIL), unrelated)) is R.INCOMPLETE
    assert pr.aggregate((pf.Check("x", R.NOT_RUN),)) is R.INCOMPLETE
    assert pr.aggregate(()) is R.INCOMPLETE


@pytest.mark.django_db
def test_every_guard_is_emitted_and_every_not_run_is_justified(stack):
    stack.redis.down = True
    report = prepare(stack)
    coverage = checks(report)["guard_coverage"]
    assert coverage.result is R.PASS, coverage.observed_sanitized
    assert pr.unjustified_not_run(report.checks) == ()
    names = set(checks(report))
    assert set(pr.PREPARE_PREREQUISITES) <= names


def test_guards_that_pass_without_a_ready_state_are_never_pass():
    report = pr.PrepareReport(
        mode=pr.Mode.INSPECT,
        guards=(pf.Check("isolation_guards", R.PASS),),
        services=None,
        reset=None,
        flags=pr.ResetFlags(),
        fixture=None,
        ready_state=None,
        final=(),
        expected_git_sha=SHA,
        git_sha=SHA,
        checked_at=NOW,
    )
    assert report.result is R.INCOMPLETE and report.detail_code == "state-not-demonstrated"


@transactional
def test_output_is_canonical_and_holds_no_personal_or_secret_material(
    stack, settings, caplog, clean_contenttype_cache
):
    with caplog.at_level("DEBUG"):
        report = prepare(stack)
    raw = report.to_bytes()
    assert raw == canonical_json_bytes(report.to_json()) and raw.count(b"\n") == 1
    text = raw.decode() + caplog.text
    specs = ex10_fixture.actor_specs()
    forbidden = [settings.SECRET_KEY, "@example.invalid", "redis://", "postgis://", "password"]
    forbidden += [spec.document_hmac for spec in specs] + [spec.email for spec in specs]
    forbidden += [str(stack.roots.base), "Traceback"]
    forbidden += [str(pk) for pk in CitizenProfile.objects.values_list("pk", flat=True)]
    leaked = [index for index, value in enumerate(forbidden) if value and value in text]
    assert leaked == []
    assert not any(check.detail_code and "/" in check.detail_code for check in report.checks)


@transactional
def test_no_token_is_issued_or_persisted(stack, monkeypatch, clean_contenttype_cache):
    def refuse(*args, **kwargs):
        raise AssertionError("la preparación no emite tokens")

    monkeypatch.setattr(AccessToken, "for_user", classmethod(refuse))
    report = prepare(stack)
    assert report.result is R.PASS, not_pass(report)
    ready = {c["check"]: c for c in report.to_json()["ready_state"]["checks"]}
    assert ready["tokens_not_persisted"]["result"] == "PASS"
    assert not any("token_blacklist" in table for table in connection.introspection.table_names())


# --- CLI ----------------------------------------------------------------------------------------


def call(stack, monkeypatch, *args):
    monkeypatch.setattr(command_module.Command, "probes", lambda self: stack.probes)
    out, err = io.StringIO(), io.StringIO()
    argv = args or (
        "--expected-git-sha",
        SHA,
        "--source-root",
        str(stack.roots.source),
        "--confirm-isolated-reset",
    )
    try:
        call_command("s11_ex10_prepare", *argv, stdout=out, stderr=err)
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


@transactional
def test_cli_prints_one_json_and_exits_0_when_ready(
    stack, monkeypatch, capsys, clean_contenttype_cache
):
    code, out, err = call(stack, monkeypatch)
    assert (code, err) == (0, "")
    assert out.count("\n") == 1 and json.loads(out)["state"] == "READY_STATE"
    # `seed_catalogs` escribe en stdout aun con verbosity=0: no llega a la salida.
    captured = capsys.readouterr()
    assert "Instituciones" not in out + captured.out + captured.err


@pytest.mark.parametrize(
    ("breakage", "code", "state"),
    [
        (lambda s: s.resolver.alive.add("worker"), 1, "NOT_READY"),
        (lambda s: setattr(s.redis, "down", True), 2, "INCOMPLETE"),
    ],
    ids=["exit-1", "exit-2"],
)
@pytest.mark.django_db
def test_cli_exit_codes_follow_the_result(stack, monkeypatch, breakage, code, state):
    breakage(stack)
    exit_code, out, err = call(stack, monkeypatch)
    assert (exit_code, json.loads(out)["state"], err) == (code, state, "")


BOTH_MODES = ("--confirm-isolated-reset", "--inspect-only")


@pytest.mark.parametrize(
    "argv",
    [
        ("--expected-git-sha", SHA, "--source-root", "/"),
        ("--expected-git-sha", SHA, "--source-root", "/", *BOTH_MODES),
        ("--expected-git-sha", "not-a-sha", "--source-root", "/", "--inspect-only"),
        ("--expected-git-sha", SHA, "--source-root", "/no/such/root", "--inspect-only"),
        ("--expected-git-sha", SHA, "--source-root", "/", "--inspect-only", "--run-id", "x"),
    ],
    ids=["no-mode", "both-modes", "bad-sha", "missing-root", "run-id"],
)
def test_cli_usage_and_configuration_errors_exit_3(stack, monkeypatch, argv):
    monkeypatch.setattr(command_module.Command, "probes", lambda self: stack.probes)
    with pytest.raises(CommandError) as error:
        call_command("s11_ex10_prepare", *argv, stdout=io.StringIO(), stderr=io.StringIO())
    assert error.value.returncode == 3
    assert "/no/such/root" not in str(error.value) and "not-a-sha" not in str(error.value)


@pytest.mark.parametrize(
    "flags",
    [
        {"confirm_isolated_reset": False},
        {"inspect_only": False},
        {"confirm_isolated_reset": True, "inspect_only": True},
    ],
    ids=["reset-false", "inspect-false", "both"],
)
def test_call_command_needs_exactly_one_mode_set_to_true(stack, monkeypatch, flags):
    monkeypatch.setattr(command_module.Command, "probes", lambda self: stack.probes)
    called = []
    monkeypatch.setattr(pr, "run_prepare", lambda *args, **kwargs: called.append(1))
    argv = ("--expected-git-sha", SHA, "--source-root", str(stack.roots.source))
    with pytest.raises(CommandError) as error:
        call_command("s11_ex10_prepare", *argv, **flags, stdout=io.StringIO(), stderr=io.StringIO())
    assert error.value.returncode == 3 and called == []


def test_cli_from_the_terminal_exits_3_with_a_sanitized_stderr():
    completed = subprocess.run(
        [sys.executable, "manage.py", "s11_ex10_prepare", f"--bogus={CANARY}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 3
    assert completed.stdout == ""
    assert completed.stderr.strip() == "S11_EX10_PREPARE_USAGE: invalid-arguments"


def test_cli_internal_errors_exit_3_without_the_original_message(stack, monkeypatch):
    def broken():
        raise RuntimeError(f"secret-path=/home/x token={CANARY}")

    stack.probes.preflight.protocol_identity = broken
    monkeypatch.setattr(command_module.Command, "probes", lambda self: stack.probes)
    with pytest.raises(CommandError) as error:
        call_command(
            "s11_ex10_prepare",
            "--expected-git-sha",
            SHA,
            "--source-root",
            str(stack.roots.source),
            "--inspect-only",
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
    assert error.value.returncode == 3
    assert str(error.value) == "S11_EX10_PREPARE_INTERNAL_ERROR: RuntimeError"


# --- Redis e infraestructura reales del proyecto Docker de tests --------------------------------


HOLD_REDIS_CLIENT = (
    "import sys, time, redis; client = redis.Redis.from_url(sys.argv[1]); client.ping(); "
    "print('ready', flush=True); time.sleep(60)"
)


@needs_redis
def test_the_real_dns_and_redis_client_probes_are_read_only_and_exact():
    # En el proyecto de tests no corre `backend`, `worker` ni `beat`: no resuelven.
    assert pr.real_resolve("redis") is True
    assert [pr.real_resolve(name) for name in pr.EX10_SERVICES] == [False, False, False]
    before = pr.real_redis_clients(REAL_REDIS)["same_db"]
    own = Redis.from_url(REAL_REDIS)
    holder = None
    try:
        own.ping()  # otra conexión de este mismo proceso: no es un servicio ajeno
        assert pr.real_redis_clients(REAL_REDIS)["same_db"] == before
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD_REDIS_CLIENT, REAL_REDIS],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert holder.stdout.readline().strip() == "ready"
        # Otro proceso conectado a la base 11, aunque sea en el mismo contenedor, sí cuenta.
        assert pr.real_redis_clients(REAL_REDIS)["same_db"] == before + 1
    finally:
        own.close()
        if holder is not None:
            holder.kill()
            holder.wait(10)


def test_own_endpoints_are_this_process_sockets():
    listener = socket.create_server(("127.0.0.1", 0))
    try:
        port = listener.getsockname()[1]
        assert ("127.0.0.1", port) in pr.own_endpoints()
    finally:
        listener.close()
    assert pr._client_endpoint("172.18.0.5:43210") == ("172.18.0.5", 43210)
    assert pr._client_endpoint("[::ffff:172.18.0.5]:43210") == ("172.18.0.5", 43210)
    assert pr._client_endpoint("garbage") is None


@needs_redis
@transactional
def test_flushdb_only_empties_the_approved_redis_db(stack, clean_contenttype_cache):
    approved, foreign = Redis.from_url(REAL_REDIS), Redis.from_url(FOREIGN_REDIS)
    foreign_key = f"s11-ex10-prepare-test:{os.getpid()}"
    try:
        approved.set("s11-ex10-prepare-test:residue", b"1")
        foreign.set(foreign_key, b"keep")
        stack.probes.writable_environment = pr.writable_environment
        stack.probes.readonly_environment = pr.readonly_environment
        stack.probes.preflight.approve_environment = pr.readonly_environment
        report = prepare(stack)
        assert report.result is R.PASS, not_pass(report)
        assert approved.dbsize() == 0
        assert foreign.get(foreign_key) == b"keep"  # nunca FLUSHALL
    finally:
        foreign.delete(foreign_key)
        approved.close()
        foreign.close()


@pytest.fixture
def real_worker():
    base = Path(django_settings.BASE_DIR) / ".s11-test-artifacts" / "ex10-prepare-worker"
    shutil.rmtree(base, ignore_errors=True)
    (base / "objects").mkdir(parents=True)
    worker_env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.dev",
        "REDIS_URL": REAL_REDIS,
        "S11_EXPERIMENTS_ENABLED": "true",
        "S11_ENVIRONMENT": "isolated-experiment",
        "S11_DATABASE_URL": "postgis://ciudadania@db:5432/ciudadania_s11_prepare_worker",
        "S11_OBJECT_ROOT": str(base / "objects"),
        "S11_FAKE_AI_ENABLED": "true",
        "AI_CLASSIFICATION_ENABLED": "false",
    }
    process = subprocess.Popen(
        ["celery", "-A", "config", "worker", "--loglevel=WARNING", "--concurrency=1"],
        cwd=django_settings.BASE_DIR,
        env=worker_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(10)
        shutil.rmtree(base)


@needs_redis
@pytest.mark.django_db
def test_a_real_worker_on_the_approved_redis_blocks_the_reset(stack, real_worker):
    row = seed_dirty_state(stack)
    stack.probes.redis_clients = pr.real_redis_clients
    deadline = time.monotonic() + 90
    while pr.real_redis_clients(REAL_REDIS)["same_db"] == 0:
        assert real_worker.poll() is None, "el worker de tests terminó al arrancar"
        assert time.monotonic() < deadline, "el worker de tests no se conectó a tiempo"
        time.sleep(0.5)
    report = prepare(stack)
    check = checks(report)["ex10_services_stopped"]
    assert (check.result, check.detail_code) == (R.FAIL, "ex10-services-active")
    assert check.observed_sanitized["redis_clients_same_db"] >= 1
    assert report.result is R.FAIL and untouched(stack, row)
