"""Reset y verificación del estado dinámico de EX-10 (fase 2B-1).

El reset sigue la estrategia del contrato vigente, `s11_environment reset` (estrategia A):
`flush` total de la base aislada, `seed_catalogs`, `reset_object_root`, `FLUSHDB` del Redis
lógico 11 y `cache.clear()`. Un reset selectivo contradiría `AuditLog`, cuyas filas no se
borran nunca en el dominio. Con `restore_fixture=True` se vuelven a preparar los diez
actores deterministas: se conservan por identidad (mismos UUID), no por filas.

Nada se ejecuta sin `approve_environment()`: aislamiento S11 (`verify_isolation`), base
conectada igual a la declarada, Redis lógico 11 local y raíz de objetos sin symlinks y
sin solapamiento con la evidencia (`var/s11-runs`, backup, restore).

`inspect_zero_state` e `inspect_ready_state` sólo leen. `INCOMPLETE` significa que una
comprobación no pudo demostrarse (Redis o la base no disponibles), nunca un estado malo.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.db.models import Q
from kombu.pidbox import Mailbox
from kombu.transport.redis import Channel
from redis import Redis
from redis.exceptions import RedisError
from rest_framework.throttling import ScopedRateThrottle

from apps.admin_app.models import AuditLog, Category, Institution
from apps.ai_client_app.models import AiClassification
from apps.auth_app.models import CitizenProfile
from apps.notifications_app.models import Notification
from apps.reports_app.models import (
    Report,
    ReportConfirmation,
    ReportMedia,
    ReportStatusHistory,
    ResolutionEvidence,
)
from apps.rewards_app.models import RewardRedemption, RewardTransaction
from config import celery_app

from . import ex10_fixture, ex10_load, runtime
from .isolation import IsolationError, reset_object_root

STATE_SCHEMA_VERSION = "s11-ex10-state-v1"
RESET_STRATEGY = "total-isolated-flush"
THROTTLE_SCOPE = "report_create"
OBJECT_ROOT_MARKER = ".s11-isolated-object-root"
#: Totales del catálogo que exige el contrato de `s11_environment` tras `seed_catalogs`.
REFERENCE_TOTALS = {"categories": 10, "institutions": 3}
#: G0, validez 5: auditoría de reportes (creación y clasificación).
_REPORT_AUDIT = Q(action__startswith="report.") | Q(action__startswith="ai.report.")
#: Todo lo que crea `POST /api/v1/reports/` y su clasificación asíncrona, y sus derivados.
_DYNAMIC_MODELS = (
    ("reports", Report),
    ("report_media", ReportMedia),
    ("report_status_history", ReportStatusHistory),
    ("resolution_evidence", ResolutionEvidence),
    ("report_confirmations", ReportConfirmation),
    ("ai_classifications", AiClassification),
    ("reward_transactions", RewardTransaction),
    ("reward_redemptions", RewardRedemption),
    ("notifications", Notification),
)


class Ex10StateError(RuntimeError):
    """Rechazo del reset o de la aprobación con un código estable, sin valores."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StateName(StrEnum):
    ZERO_STATE = "ZERO_STATE"
    READY_STATE = "READY_STATE"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


class RedisLike(Protocol):
    def ping(self) -> Any: ...
    def llen(self, name: str) -> int: ...
    def hlen(self, name: str) -> int: ...
    def zcard(self, name: str) -> int: ...
    def scan_iter(self, match: str, count: int) -> Any: ...
    def flushdb(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class StateCheck:
    check: str
    expected: Any
    observed: Any
    result: CheckStatus
    detail: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "expected": self.expected,
            "observed": self.observed,
            "result": str(self.result),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class StateReport:
    state: StateName
    checks: tuple[StateCheck, ...]

    @property
    def overall(self) -> CheckStatus:
        results = {check.result for check in self.checks}
        if CheckStatus.FAIL in results:
            return CheckStatus.FAIL
        if CheckStatus.INCOMPLETE in results:
            return CheckStatus.INCOMPLETE
        return CheckStatus.PASS

    def by_name(self) -> dict[str, StateCheck]:
        return {check.check: check for check in self.checks}

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "protocol_id": ex10_load.PROTOCOL_ID,
            "state": str(self.state),
            "overall": str(self.overall),
            "checks": [check.to_json() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class ApprovedEnvironment:
    """Entorno que superó todas las guardas. Sólo lo construye `approve_environment`."""

    database_name: str
    object_root: Path = field(repr=False)
    redis: RedisLike = field(repr=False)

    def __repr__(self) -> str:
        return f"ApprovedEnvironment(database={self.database_name}, redis_db=11)"


@dataclass(frozen=True, slots=True)
class ResetResult:
    restore_fixture: bool
    fixture: ex10_fixture.FixtureResult | None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "protocol_id": ex10_load.PROTOCOL_ID,
            "strategy": RESET_STRATEGY,
            "steps": [
                "flush",
                "seed_catalogs",
                "reset_object_root",
                "redis_flushdb",
                "cache_clear",
                *(["prepare_fixture"] if self.restore_fixture else []),
            ],
            "fixture": None if self.fixture is None else self.fixture.to_json(),
        }


# --- Guardas -----------------------------------------------------------------------------


def approve_environment(
    *, redis_factory: Callable[[str], RedisLike] = Redis.from_url
) -> ApprovedEnvironment:
    """Aplica todas las guardas antes de tocar nada; cualquier duda aborta."""
    try:
        report = runtime.require_isolation()
    except IsolationError:
        raise Ex10StateError("ISOLATION_REJECTED") from None
    # La URL declarada no basta: la base realmente conectada debe ser la aislada.
    if _connected_database_name() != report.database_name:
        raise Ex10StateError("DATABASE_NOT_APPROVED")
    config = runtime.isolation_config()
    if config.object_root.is_symlink():
        raise Ex10StateError("STORAGE_SYMLINK")
    root = Path(report.object_root)
    for evidence_root in (
        settings.S11_ARTIFACT_ROOT,
        settings.S11_BACKUP_ROOT,
        settings.S11_RESTORE_ROOT,
    ):
        evidence = Path(evidence_root).resolve()
        if root == evidence or evidence in root.parents or root in evidence.parents:
            raise Ex10StateError("STORAGE_OVERLAPS_EVIDENCE")
    return ApprovedEnvironment(report.database_name, root, redis_factory(config.redis_url))


def _connected_database_name() -> str:
    return connection.settings_dict["NAME"]


def _require_still_approved(environment: ApprovedEnvironment) -> None:
    try:
        runtime.require_isolation()
    except IsolationError:
        raise Ex10StateError("ISOLATION_REJECTED") from None
    if _connected_database_name() != environment.database_name:
        raise Ex10StateError("DATABASE_NOT_APPROVED")


# --- Reset ---------------------------------------------------------------------------------


def reset_ex10_state(
    environment: ApprovedEnvironment,
    *,
    restore_fixture: bool = False,
    dataset: ex10_load.LoadDataset | None = None,
) -> ResetResult:
    """Reset total del estado aislado (estrategia A) y, si se pide, el fixture.

    Todo lo que puede fallar se comprueba antes del primer paso destructivo: dataset,
    marcador y symlinks del storage y disponibilidad de Redis.
    """
    _require_still_approved(environment)
    if restore_fixture:
        ex10_fixture.actor_specs(dataset)
    objects = _scan_objects(environment.object_root)
    if not objects.marker:
        raise Ex10StateError("STORAGE_NOT_INITIALIZED")
    if objects.symlinks:
        raise Ex10StateError("STORAGE_SYMLINK")
    try:
        environment.redis.ping()
    except (RedisError, OSError):
        raise Ex10StateError("REDIS_UNAVAILABLE") from None

    call_command("flush", interactive=False, verbosity=0)
    call_command("seed_catalogs", verbosity=0)
    try:
        reset_object_root(runtime.isolation_config())
    except (IsolationError, OSError):
        raise Ex10StateError("STORAGE_RESET_FAILED") from None
    try:
        environment.redis.flushdb()
    except (RedisError, OSError):
        raise Ex10StateError("REDIS_UNAVAILABLE") from None
    cache.clear()
    fixture = ex10_fixture.prepare_fixture(dataset) if restore_fixture else None
    return ResetResult(restore_fixture, fixture)


# --- Inspección ----------------------------------------------------------------------------


def inspect_zero_state(
    environment: ApprovedEnvironment, *, dataset: ex10_load.LoadDataset | None = None
) -> StateReport:
    """Antes del fixture: referencia presente, cero estado dinámico y cero usuarios."""
    dataset = ex10_load.load_dataset() if dataset is None else dataset
    return StateReport(
        StateName.ZERO_STATE,
        (
            *_reference_checks(dataset),
            *_dynamic_checks(),
            _count_check("users_total", lambda: get_user_model().objects.count(), 0),
            _count_check("citizen_profiles", lambda: CitizenProfile.objects.count(), 0),
            *_redis_checks(environment, actor_ids=()),
            *_storage_checks(environment),
        ),
    )


def inspect_ready_state(
    environment: ApprovedEnvironment, *, dataset: ex10_load.LoadDataset | None = None
) -> StateReport:
    """Tras el fixture: el estado dinámico de ZERO_STATE más exactamente los 10 actores."""
    dataset = ex10_load.load_dataset() if dataset is None else dataset
    specs = ex10_fixture.actor_specs(dataset)
    total = ex10_load.VIRTUAL_USERS
    return StateReport(
        StateName.READY_STATE,
        (
            *_reference_checks(dataset),
            *_dynamic_checks(),
            _count_check("users_total", lambda: get_user_model().objects.count(), total),
            _count_check("citizen_profiles", lambda: CitizenProfile.objects.count(), total),
            _actors_check(specs),
            _tokens_not_persisted_check(),
            *_redis_checks(environment, actor_ids=tuple(str(s.user_id) for s in specs)),
            *_storage_checks(environment),
        ),
    )


def _count_check(name: str, count: Callable[[], int], expected: int) -> StateCheck:
    try:
        observed = count()
    except DatabaseError:
        return StateCheck(name, expected, None, CheckStatus.INCOMPLETE, "database-unavailable")
    result = CheckStatus.PASS if observed == expected else CheckStatus.FAIL
    return StateCheck(name, expected, observed, result)


def _reference_checks(dataset: ex10_load.LoadDataset) -> list[StateCheck]:
    rows = dataset.payload["requests"]
    categories = sorted({row["category_slug"] for row in rows})
    institutions = sorted({row["institution_slug"] for row in rows})
    return [
        _count_check(
            "required_categories_active",
            lambda: Category.objects.filter(slug__in=categories, is_active=True).count(),
            len(categories),
        ),
        _count_check(
            "required_institutions",
            lambda: Institution.objects.filter(slug__in=institutions).count(),
            len(institutions),
        ),
        _count_check(
            "categories_total", lambda: Category.objects.count(), REFERENCE_TOTALS["categories"]
        ),
        _count_check(
            "institutions_total",
            lambda: Institution.objects.count(),
            REFERENCE_TOTALS["institutions"],
        ),
    ]


def _dynamic_checks() -> list[StateCheck]:
    checks = [
        _count_check(name, lambda model=model: model.objects.count(), 0)
        for name, model in _DYNAMIC_MODELS
    ]
    checks.append(
        _count_check("audit_logs_report", lambda: AuditLog.objects.filter(_REPORT_AUDIT).count(), 0)
    )
    return checks


def _actors_check(specs: tuple[ex10_fixture.ActorSpec, ...]) -> StateCheck:
    try:
        inspected = [ex10_fixture.inspect_actor(spec) for spec in specs]
    except DatabaseError:
        return StateCheck(
            "fixture_actors", len(specs), None, CheckStatus.INCOMPLETE, "database-unavailable"
        )
    missing = sum(1 for exists, _ in inspected if not exists)
    drifted = sum(1 for exists, drift in inspected if exists and drift)
    matched = len(specs) - missing - drifted
    return StateCheck(
        "fixture_actors",
        len(specs),
        matched,
        CheckStatus.PASS if matched == len(specs) else CheckStatus.FAIL,
        f"missing={missing} drift={drifted}",
    )


def _tokens_not_persisted_check() -> StateCheck:
    # SimpleJWT no persiste access tokens salvo con `token_blacklist`, que no se instala.
    try:
        tables = connection.introspection.table_names()
    except DatabaseError:
        return StateCheck(
            "tokens_not_persisted", True, None, CheckStatus.INCOMPLETE, "database-unavailable"
        )
    persisted = "rest_framework_simplejwt.token_blacklist" in settings.INSTALLED_APPS or any(
        "token_blacklist" in table for table in tables
    )
    return StateCheck(
        "tokens_not_persisted",
        True,
        not persisted,
        CheckStatus.FAIL if persisted else CheckStatus.PASS,
    )


def throttle_key(ident: str) -> str:
    """Clave de `ScopedRateThrottle` para `report_create`, tal como la usa la API de cache."""
    return ScopedRateThrottle.cache_format % {"scope": THROTTLE_SCOPE, "ident": ident}


def throttle_redis_pattern() -> str:
    """Patrón `SCAN` de esas claves en Redis, con el prefijo y la versión de la cache."""
    return cache.make_key(throttle_key("")) + "*"


@dataclass(frozen=True, slots=True)
class BrokerKeys:
    """Claves reales del transporte Redis de Kombu y del result backend de Celery.

    Se derivan de las clases instaladas y de `broker_transport_options`, nunca de nombres
    escritos a mano. Tres clases:

    A. `queues`, `unacked`, `unacked_index` y `result_patterns`: estado dinámico que
       ZERO_STATE y READY_STATE exigen vacío.
    B. `static_bindings`: tablas `_kombu.binding.*` que declaran web y worker; pueden
       existir. `FLUSHDB` las borra, y como Kombu no tiene cola de mensajes muertos, un
       proceso que ya las declaró publicaría sin destino: tras el reset hay que arrancar
       web y worker, no reutilizarlos.
    C. `worker_ephemeral`: mutex de restauración, colas pidbox y de eventos de un worker
       vivo. Las verifica el preflight de la fase 2B-2, no estos estados.
    """

    queues: tuple[str, ...]
    unacked: str
    unacked_index: str
    #: `None`: el result backend no guarda en este Redis y no se puede inspeccionar.
    result_patterns: tuple[str, ...] | None
    static_bindings: str
    worker_ephemeral: tuple[str, ...]


def broker_keys(app=celery_app) -> BrokerKeys:
    options = dict(app.conf.broker_transport_options or {})

    def option(name: str):
        return options.get(name, getattr(Channel, name))

    prefix, sep, queue = option("global_keyprefix"), option("sep"), app.conf.task_default_queue
    pidbox = Mailbox.exchange_fmt % app.conf.control_exchange
    return BrokerKeys(
        # Una lista por paso de prioridad, como `Channel._q_for_pri` y `Channel._size`.
        queues=tuple(
            prefix + (f"{queue}{sep}{step}" if step else queue) for step in option("priority_steps")
        ),
        unacked=prefix + option("unacked_key"),
        unacked_index=prefix + option("unacked_index_key"),
        result_patterns=_result_patterns(app),
        static_bindings=prefix + Channel.keyprefix_queue % "*",
        worker_ephemeral=(
            prefix + option("unacked_mutex_key"),
            f"{prefix}*.{pidbox}",  # colas de pidbox, incluidas las de respuesta
            f"{prefix}{app.conf.event_queue_prefix}.*",
        ),
    )


def _result_patterns(app) -> tuple[str, ...] | None:
    if not app.conf.result_backend:
        return ()
    if app.conf.result_backend != app.conf.broker_url:
        return None
    # Los prefijos del backend ya llevan el `global_keyprefix` de Celery, si lo hubiera.
    backend = app.backend
    prefixes = [getattr(backend, f"{name}_keyprefix", None) for name in ("task", "group", "chord")]
    if None in prefixes:
        return None
    return tuple((p.decode() if isinstance(p, bytes) else p) + "*" for p in prefixes)


def _scan_count(redis: RedisLike, patterns: tuple[str, ...]) -> int:
    return sum(1 for pattern in patterns for _ in redis.scan_iter(match=pattern, count=500))


def _redis_checks(environment: ApprovedEnvironment, *, actor_ids: tuple[str, ...]) -> list:
    keys, redis = broker_keys(), environment.redis
    counters = {
        "celery_queue_length": lambda: sum(redis.llen(name) for name in keys.queues),
        "celery_unacked": lambda: redis.hlen(keys.unacked),
        "celery_unacked_index": lambda: redis.zcard(keys.unacked_index),
        "throttle_keys": lambda: _scan_count(redis, (throttle_redis_pattern(),)),
    }
    if keys.result_patterns:
        counters["celery_result_keys"] = lambda: _scan_count(redis, keys.result_patterns)
    checks = []
    if keys.result_patterns is None:
        checks.append(
            StateCheck(
                "celery_result_keys",
                0,
                None,
                CheckStatus.INCOMPLETE,
                "result-backend-not-inspected",
            )
        )
    try:
        redis.ping()
        observed = {name: count() for name, count in counters.items()}
    except (RedisError, OSError):
        return checks + [
            StateCheck(name, 0, None, CheckStatus.INCOMPLETE, "redis-unavailable")
            for name in counters
        ]
    checks += [
        StateCheck(name, 0, value, CheckStatus.PASS if value == 0 else CheckStatus.FAIL)
        for name, value in observed.items()
    ]
    if actor_ids:
        # Por la API de cache: vale para cualquier backend, no sólo Redis.
        try:
            residual = sum(1 for ident in actor_ids if cache.get(throttle_key(ident)) is not None)
        except (RedisError, OSError):
            checks.append(
                StateCheck(
                    "throttle_actor_keys", 0, None, CheckStatus.INCOMPLETE, "cache-unavailable"
                )
            )
        else:
            checks.append(
                StateCheck(
                    "throttle_actor_keys",
                    0,
                    residual,
                    CheckStatus.PASS if residual == 0 else CheckStatus.FAIL,
                )
            )
    return checks


@dataclass(frozen=True, slots=True)
class _ObjectScan:
    marker: bool
    objects: int
    symlinks: int


def _scan_objects(root: Path) -> _ObjectScan:
    """Recorre la raíz sin seguir enlaces; cuenta objetos y symlinks, nunca los lee."""
    if not root.is_dir() or root.is_symlink():
        return _ObjectScan(False, 0, 1 if root.is_symlink() else 0)
    objects = symlinks = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = Path(directory) / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                symlinks += 1
            elif not stat.S_ISDIR(mode) and path != root / OBJECT_ROOT_MARKER:
                objects += 1
    return _ObjectScan((root / OBJECT_ROOT_MARKER).is_file(), objects, symlinks)


def _storage_checks(environment: ApprovedEnvironment) -> list[StateCheck]:
    try:
        scan = _scan_objects(environment.object_root)
    except OSError:
        names = ("object_root_marker", "residual_objects", "object_symlinks")
        return [
            StateCheck(n, None, None, CheckStatus.INCOMPLETE, "storage-unreadable") for n in names
        ]
    return [
        StateCheck(
            "object_root_marker",
            True,
            scan.marker,
            CheckStatus.PASS if scan.marker else CheckStatus.FAIL,
        ),
        StateCheck(
            "residual_objects",
            0,
            scan.objects,
            CheckStatus.PASS if scan.objects == 0 else CheckStatus.FAIL,
        ),
        StateCheck(
            "object_symlinks",
            0,
            scan.symlinks,
            CheckStatus.PASS if scan.symlinks == 0 else CheckStatus.FAIL,
        ),
    ]
