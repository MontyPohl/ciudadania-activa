"""Preparación operacional de EX-10 (fase 2B-3). Mutante; el preflight sigue siendo read-only.

Lleva el entorno S11 aislado, desde cualquier estado previo permitido, hasta `READY_STATE`
con el contrato de reset ya aprobado (`ex10_state.reset_ex10_state`, estrategia A): `flush`
de la base experimental, `seed_catalogs`, `reset_object_root`, `FLUSHDB` del Redis 11,
`cache.clear()` y los diez ciudadanos deterministas del dataset. Después vuelve a leer el
estado sin escribir nada. Nunca crea `run_id`, manifest, evidencia, tokens persistidos,
requests, reportes ni llamadas a un proveedor.

Antes del primer paso destructivo, y dentro de `SideEffectGuard`, corren las guardas: las del
preflight (repositorio, protocolo, dataset y plan, aislamiento), el código de la preparación
igual al del checkout, el esquema migrado, la URL de Redis exacta del protocolo (sin query:
en redis-py un `?db=` ganaría a la ruta), la caché sobre ese Redis, el storage
marcado y sin symlinks, ningún run EX-10 abierto ni transacción de custodia pendiente y
ningún `backend`, `worker` o `beat` vivo. Kombu no tiene cola de mensajes muertos: un
`FLUSHDB` con la web o el worker en marcha perdería en silencio sus tareas. Por eso se
prepara con la base y Redis arriba y web y worker todavía detenidos, y se arrancan después.

El modo `INSPECT` es sólo lectura: las mismas guardas salvo la de servicios detenidos, que
sólo se informa, y la inspección de `READY_STATE` y del broker. Salida: un JSON por stdout;
exit 0 PASS / `READY_STATE`, 1 FAIL / `NOT_READY`, 2 INCOMPLETE y 3 uso o configuración. Un
check `NOT_RUN` sigue la regla del preflight: sólo lo justifica un prerrequisito FAIL o
INCOMPLETE, y nunca es un resultado global.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import ipaddress
import os
import re
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from celery.app.base import Celery
from celery.app.task import Task
from celery.signals import before_task_publish
from django.conf import settings
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from redis import Redis
from redis.exceptions import RedisError

from . import ex10_fixture, ex10_load, ex10_preflight, ex10_state, ex10_worker_probe, runtime
from .canonical import canonical_json_bytes

SCHEMA_VERSION = "s11c3-ex10-prepare-v1"
EXIT_PASS, EXIT_FAIL, EXIT_INCOMPLETE, EXIT_USAGE = 0, 1, 2, 3
#: Servicios de `infra/docker-compose.yml` que publican o consumen en el broker.
EX10_SERVICES = ("backend", "worker", "beat")
#: El código propio de la preparación, comparado byte a byte con el checkout como
#: `RUNNING_SOURCES` en el preflight.
PREPARE_SOURCES = (
    "backend/apps/experiments_app/ex10_prepare.py",
    "backend/apps/experiments_app/management/commands/s11_ex10_prepare.py",
)
#: Pasos del preflight que son también guardas de la preparación, con sus checks y reglas.
GUARD_STEPS = ("repository", "protocol", "dataset_and_plan", "isolation")
REDIS_TIMEOUT_SECONDS = 5.0
#: `getaddrinfo` sin respuesta del DNS de Docker para ese nombre: ningún contenedor vivo lo
#: lleva como alias. Sólo vale si el nombre de control (Redis) sí resuelve en la misma
#: comprobación; `EAI_AGAIN` es lo que devuelve el DNS embebido cuando reenvía y falla.
_ABSENT_NAME_ERRNOS = frozenset(
    code
    for code in (
        getattr(socket, "EAI_NONAME", None),
        getattr(socket, "EAI_AGAIN", None),
        getattr(socket, "EAI_NODATA", None),
        getattr(socket, "EAI_FAIL", None),
    )
    if code is not None
)
#: `pg_try_advisory_lock` de sesión: dos preparaciones nunca resetean a la vez la misma base.
_LOCK_KEY = int.from_bytes(
    hashlib.sha256(f"{ex10_load.PROTOCOL_ID}:prepare".encode()).digest()[:8], "big", signed=True
)
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_RUN_LOCK = threading.Lock()
_RESULT = ex10_preflight.CheckResult
_BLOCKING = frozenset({_RESULT.FAIL, _RESULT.INCOMPLETE})
_BLOCKED_BY = "blocked-by:"
#: Checks propios de la preparación y sus prerrequisitos (además de los del preflight).
PREPARE_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "prepare_running_source": ("repository_source_complete",),
    "database_schema_current": ("isolation_guards", "isolation_database_connected"),
    # La URL exacta del protocolo: `redis://redis:6379/11` sin query (un `?db=` ganaría a la
    # ruta en redis-py y el `FLUSHDB` iría a otra base) ni credenciales.
    "redis_endpoint_exact": ("isolation_guards",),
    "cache_approved": ("isolation_guards",),
    "storage_contents": ("isolation_guards", "isolation_storage_roots"),
    "evidence_idle": ("isolation_guards",),
    # Sin Redis aprobado no se le pregunta nada: no se sabe a qué servidor se hablaría.
    "ex10_services_stopped": ("isolation_guards", "isolation_redis"),
    "guard_no_side_effects": (),
    "guard_coverage": (),
}
#: Resets que dejan el estado sin demostrar (infraestructura), no un estado malo.
_INCOMPLETE_RESET_CODES = frozenset({"REDIS_UNAVAILABLE", "STORAGE_RESET_FAILED"})


class Mode(StrEnum):
    PREPARE = "PREPARE"
    INSPECT = "INSPECT"


class PrepareState(StrEnum):
    READY_STATE = "READY_STATE"
    NOT_READY = "NOT_READY"
    INCOMPLETE = "INCOMPLETE"


_OUTCOMES = {
    _RESULT.PASS: (PrepareState.READY_STATE, EXIT_PASS),
    _RESULT.FAIL: (PrepareState.NOT_READY, EXIT_FAIL),
    _RESULT.INCOMPLETE: (PrepareState.INCOMPLETE, EXIT_INCOMPLETE),
}


class PrepareError(Exception):
    """Error de uso o de configuración de la preparación (exit 3). Sólo códigos."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PrepareBusy(PrepareError):
    def __init__(self):
        super().__init__("prepare-already-running")


class _PublishAttempted(Exception):
    """Una tarea intentó publicarse durante el reset; se bloqueó antes de salir."""


def _code(value: str) -> str:
    return value.lower().replace("_", "-")


# --- Adapters reales ---------------------------------------------------------------------------


class PrepareRepository(ex10_preflight.GitRepository):
    """`GitRepository` del preflight más la comprobación de rutas versionadas."""

    def untracked(self, paths: tuple[str, ...]) -> int:
        """Cuántas de `paths` no están en el índice del checkout."""
        output = self._git("ls-files", "-z", "--", *paths)[1]
        tracked = {item for item in output.split("\0") if item}
        return sum(1 for path in paths if path not in tracked)


def real_resolve(name: str) -> bool | None:
    """`True` si el nombre resuelve, `False` si el DNS no lo conoce, `None` si no se sabe."""
    try:
        return bool(socket.getaddrinfo(name, None))
    except socket.gaierror as exc:
        return False if exc.errno in _ABSENT_NAME_ERRNOS else None
    except (OSError, UnicodeError):
        return None


def _client(url: str) -> Redis:
    return Redis.from_url(
        url, socket_connect_timeout=REDIS_TIMEOUT_SECONDS, socket_timeout=REDIS_TIMEOUT_SECONDS
    )


def _close(redis_client: Any) -> None:
    inner = getattr(redis_client, "_client", redis_client)
    close = getattr(inner, "close", None)
    if callable(close):
        with contextlib.suppress(RedisError, OSError):
            close()


def _proc_address(hexaddr: str) -> str:
    """Dirección de `/proc/net/tcp{,6}`: palabras de 32 bits en el orden del host."""
    raw = bytes.fromhex(hexaddr)
    words = b"".join(raw[index : index + 4][::-1] for index in range(0, len(raw), 4))
    address = ipaddress.ip_address(words)
    mapped = getattr(address, "ipv4_mapped", None)
    return str(mapped or address)


def own_endpoints() -> set[tuple[str, int]]:
    """`(ip, puerto)` locales de los sockets TCP de este proceso, leídos de `/proc`.

    Sólo este proceso: un worker lanzado como subproceso en el mismo contenedor tiene
    otros sockets y sigue contando como ajeno. Sin `/proc` el conjunto queda vacío y las
    conexiones propias cuentan como ajenas: falla cerrado.
    """
    inodes = set()
    with contextlib.suppress(OSError):
        for fd in os.listdir("/proc/self/fd"):
            with contextlib.suppress(OSError):
                target = os.readlink(f"/proc/self/fd/{fd}")
                if target.startswith("socket:[") and target.endswith("]"):
                    inodes.add(target[8:-1])
    endpoints = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        with contextlib.suppress(OSError, ValueError):
            for line in Path(table).read_text().splitlines()[1:]:
                fields = line.split()
                if len(fields) > 9 and fields[9] in inodes:
                    address, _, port = fields[1].partition(":")
                    endpoints.add((_proc_address(address), int(port, 16)))
    return endpoints


def _client_endpoint(addr: str) -> tuple[str, int] | None:
    host, _, port = addr.rpartition(":")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
        return str(getattr(address, "ipv4_mapped", None) or address), int(port)
    except ValueError:
        return None


def real_redis_clients(url: str) -> dict[str, int]:
    """Clientes del Redis aprobado que no son este proceso: en su base lógica y en total.

    Las demás conexiones de la preparación (la aprobación, la caché) no cuentan; se
    reconocen por sus extremos TCP locales. Sólo cantidades.
    """
    db = ex10_worker_probe.endpoint_facts(url)["db"]
    client = _client(url)
    try:
        probe = int(client.client_id())
        entries = client.client_list()
        own = own_endpoints()
    finally:
        _close(client)
    foreign = [
        entry
        for entry in entries
        if int(entry.get("id", -1)) != probe
        and _client_endpoint(str(entry.get("addr", ""))) not in own
    ]
    return {
        "same_db": sum(1 for entry in foreign if str(entry.get("db")) == str(db)),
        "other_clients": len(foreign),
    }


def real_pending_migrations() -> int:
    # Sólo lee `django_migrations`: el `MigrationRecorder` no crea la tabla para consultarla.
    executor = MigrationExecutor(connection)
    return len(executor.migration_plan(executor.loader.graph.leaf_nodes()))


def readonly_environment() -> ex10_state.ApprovedEnvironment:
    return ex10_state.approve_environment(
        redis_factory=lambda url: ex10_preflight.ReadOnlyRedis(_client(url))
    )


def writable_environment() -> ex10_state.ApprovedEnvironment:
    """El único cliente con `FLUSHDB`, y sólo sobre la base lógica aprobada (11)."""
    return ex10_state.approve_environment(redis_factory=_client)


@contextlib.contextmanager
def advisory_lock() -> Iterator[bool]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_LOCK_KEY])
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            # Si la conexión cayó durante el reset, el lock de sesión murió con ella: que
            # liberarlo falle no puede tapar el resultado INCOMPLETE ya calculado.
            with contextlib.suppress(DatabaseError), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_LOCK_KEY])


@dataclass
class PrepareProbes:
    """Todo lo que la preparación observa o ejecuta; los tests sustituyen cada pieza."""

    preflight: ex10_preflight.Probes = field(
        default_factory=lambda: ex10_preflight.Probes(repository=PrepareRepository)
    )
    resolve: Callable[[str], bool | None] = real_resolve
    redis_clients: Callable[[str], dict[str, int]] = real_redis_clients
    pending_migrations: Callable[[], int] = real_pending_migrations
    readonly_environment: Callable[[], ex10_state.ApprovedEnvironment] = readonly_environment
    writable_environment: Callable[[], ex10_state.ApprovedEnvironment] = writable_environment
    exclusive_lock: Callable[[], contextlib.AbstractContextManager[bool]] = advisory_lock
    reset: Callable[..., ex10_state.ResetResult] = ex10_state.reset_ex10_state
    inspect_ready_state: Callable[..., ex10_state.StateReport] = ex10_state.inspect_ready_state
    classify_broker: Callable[[Any], ex10_preflight.BrokerSnapshot] = ex10_preflight.classify_broker
    clock: Callable[[], datetime] = timezone.now


@dataclass(frozen=True, slots=True)
class PrepareOptions:
    expected_git_sha: str
    source_root: Path
    mode: Mode = Mode.PREPARE


# --- Resultado ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResetFlags:
    database_reset: bool | None = False
    catalogs_seeded: bool | None = False
    object_root_reset: bool | None = False
    redis_reset: bool | None = False
    cache_reset: bool | None = False

    @classmethod
    def unknown(cls) -> ResetFlags:
        """Un reset interrumpido: qué pasos llegaron a aplicarse no está demostrado."""
        return cls(None, None, None, None, None)

    @classmethod
    def from_steps(cls, steps: list[str]) -> ResetFlags:
        return cls(
            "flush" in steps,
            "seed_catalogs" in steps,
            "reset_object_root" in steps,
            "redis_flushdb" in steps,
            "cache_clear" in steps,
        )

    @property
    def complete(self) -> bool:
        return all(value is True for value in dataclasses.astuple(self))


def _allowed_blockers(name: str) -> frozenset[str]:
    if name in ex10_preflight.PREREQUISITES:
        return ex10_preflight.allowed_blockers(name)
    seen: set[str] = set()
    pending = list(PREPARE_PREREQUISITES.get(name, ()))
    while pending:
        item = pending.pop()
        if item in seen:
            continue
        seen.add(item)
        if item in ex10_preflight.PREREQUISITES:
            seen |= ex10_preflight.allowed_blockers(item)
        else:
            pending.extend(PREPARE_PREREQUISITES.get(item, ()))
    return frozenset(seen)


def unjustified_not_run(checks: tuple[ex10_preflight.Check, ...]) -> tuple[str, ...]:
    """Los NOT_RUN sin una causa declarada que haya quedado FAIL o INCOMPLETE."""
    results = {check.check: check.result for check in checks}
    unjustified = []
    for check in checks:
        if check.result is not _RESULT.NOT_RUN:
            continue
        detail = check.detail_code or ""
        blocker = detail.removeprefix(_BLOCKED_BY) if detail.startswith(_BLOCKED_BY) else None
        if (
            blocker is None
            or results.get(blocker) not in _BLOCKING
            or blocker not in _allowed_blockers(check.check)
        ):
            unjustified.append(check.check)
    return tuple(unjustified)


def aggregate(checks: tuple[ex10_preflight.Check, ...]) -> ex10_preflight.CheckResult:
    """INCOMPLETE > FAIL > PASS, con la regla de NOT_RUN del preflight."""
    decisive = {check.result for check in checks if check.result is not _RESULT.NOT_RUN}
    if not checks or _RESULT.INCOMPLETE in decisive or unjustified_not_run(checks):
        return _RESULT.INCOMPLETE
    if _RESULT.FAIL in decisive:
        return _RESULT.FAIL
    return _RESULT.PASS


@dataclass(frozen=True, slots=True)
class PrepareReport:
    mode: Mode
    guards: tuple[ex10_preflight.Check, ...]
    #: Observación de servicios vivos; en PREPARE además es la guarda `ex10_services_stopped`.
    services: dict[str, Any] | None
    #: Resultado de la fase de reset: `None` si no corrió (INSPECT o guardas no superadas).
    reset: ex10_preflight.Check | None
    flags: ResetFlags
    fixture: ex10_fixture.FixtureResult | None
    ready_state: ex10_state.StateReport | None
    final: tuple[ex10_preflight.Check, ...]
    expected_git_sha: str
    git_sha: str | None
    checked_at: datetime

    @property
    def checks(self) -> tuple[ex10_preflight.Check, ...]:
        return (*self.guards, *((self.reset,) if self.reset else ()), *self.final)

    @property
    def result(self) -> ex10_preflight.CheckResult:
        outcome = aggregate(self.checks)
        if outcome is _RESULT.PASS and (
            self.ready_state is None or (self.mode is Mode.PREPARE and self.reset is None)
        ):
            # Sin READY_STATE leído (o, al preparar, sin reset) nada demuestra el estado.
            return _RESULT.INCOMPLETE
        return outcome

    @property
    def state(self) -> PrepareState:
        return _OUTCOMES[self.result][0]

    @property
    def exit_code(self) -> int:
        return _OUTCOMES[self.result][1]

    @property
    def detail_code(self) -> str | None:
        """La primera causa que impide PASS: `<check>:<código>`."""
        for check in self.checks:
            if check.result in _BLOCKING:
                return f"{check.check}:{check.detail_code or 'not-pass'}"
        unjustified = unjustified_not_run(self.checks)
        if unjustified:
            return f"{unjustified[0]}:unexpected-not-run"
        return None if self.result is _RESULT.PASS else "state-not-demonstrated"

    def to_json(self) -> dict[str, Any]:
        fixture = self.fixture
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": str(self.mode),
            "scenario_id": ex10_load.SCENARIO_ID,
            "protocol_version": ex10_load.PROTOCOL_ID,
            "methodology_version": ex10_load.METHODOLOGY_VERSION,
            "dataset_id": ex10_load.DATASET_ID,
            "dataset_sha256": ex10_load.DATASET_SHA256,
            "result": str(self.result),
            "state": str(self.state),
            "detail_code": self.detail_code,
            "expected_git_sha": self.expected_git_sha,
            "git_sha": self.git_sha,
            "database_reset": self.flags.database_reset,
            "catalogs_seeded": self.flags.catalogs_seeded,
            "object_root_reset": self.flags.object_root_reset,
            "redis_reset": self.flags.redis_reset,
            "cache_reset": self.flags.cache_reset,
            "fixture_result": None if fixture is None else _fixture_summary(fixture),
            # Alias y UUID del dataset: nunca emails, perfiles, `document_hmac` ni tokens.
            "actors": (
                None
                if fixture is None
                else [
                    {"alias": actor.alias, "user_id": actor.user_id, "result": str(actor.result)}
                    for actor in fixture.actors
                ]
            ),
            "ready_state": None if self.ready_state is None else self.ready_state.to_json(),
            "services": self.services,
            "guards": [check.to_json() for check in self.guards],
            "reset": None if self.reset is None else self.reset.to_json(),
            "final_checks": [check.to_json() for check in self.final],
            # Invariantes: la preparación nunca ejecuta ni registra EX-10.
            "run_id": None,
            "evidence_written": False,
            "report_posts": 0,
            "provider_calls": 0,
            "task_publishes": 0,
            "checked_at": self.checked_at.isoformat().replace("+00:00", "Z"),
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())


def _fixture_summary(fixture: ex10_fixture.FixtureResult) -> dict[str, Any]:
    counts = {result: 0 for result in ex10_fixture.ActorResult}
    for actor in fixture.actors:
        counts[actor.result] += 1
    return {
        "schema_version": ex10_fixture.FIXTURE_SCHEMA_VERSION,
        "outcome": str(fixture.outcome),
        "count": len(fixture.actors),
        **{_code(str(result)): count for result, count in counts.items()},
    }


# --- Guardas ------------------------------------------------------------------------------------


def _protected_roots(options: PrepareOptions) -> tuple[Path, ...]:
    return (
        Path(settings.S11_ARTIFACT_ROOT),
        Path(settings.S11_BACKUP_ROOT),
        Path(settings.S11_RESTORE_ROOT),
        Path(settings.S11_OBJECT_ROOT),
        options.source_root,
    )


def _database_host() -> str | None:
    """El host de la base, sólo si es el declarado para S11 (el que aprueba el aislamiento).

    psycopg 3.2 resuelve el nombre del host con `socket.getaddrinfo` desde Python al abrir
    la conexión. En un proceso nuevo esa primera conexión ocurre dentro de la guarda, que
    sin este host la bloquearía como red no aprobada. Si el host conectado no es el
    declarado, no se permite y la conexión queda bloqueada: falla cerrado.
    """
    configured = settings.DATABASES["default"].get("HOST") or ""
    declared = urlsplit(runtime.isolation_config().database_url).hostname or ""
    return configured if configured and configured == declared else None


def _allowed_hosts() -> frozenset[str]:
    hosts = {urlsplit(settings.REDIS_URL).hostname or "", _database_host() or "", *EX10_SERVICES}
    return frozenset(host for host in hosts if host)


def _side_effect_guard(options: PrepareOptions) -> ex10_preflight.SideEffectGuard:
    return ex10_preflight.SideEffectGuard(
        protected_roots=_protected_roots(options), allowed_hosts=_allowed_hosts()
    )


def services_check(probes: PrepareProbes) -> ex10_preflight.Check:
    """Ningún `backend`, `worker` ni `beat` vivo que pueda publicar durante el `FLUSHDB`.

    Dos señales independientes: el DNS de Docker (un servicio vivo en la red del proyecto
    resuelve por su nombre; el propio contenedor de la preparación no lleva ese alias) y
    los clientes conectados a la base lógica aprobada de Redis. El launcher del host añade
    una tercera con `docker ps`.
    """
    name = "ex10_services_stopped"
    control = urlsplit(settings.REDIS_URL).hostname or ""
    resolved = {service: probes.resolve(service) for service in EX10_SERVICES}
    control_ok = probes.resolve(control) is True
    try:
        clients = probes.redis_clients(settings.REDIS_URL)
    except (RedisError, OSError, ValueError):
        clients = None
    observed = {
        "dns_control_resolves": control_ok,
        **{f"{service}_resolves": resolved[service] for service in EX10_SERVICES},
        "redis_clients_same_db": None if clients is None else clients["same_db"],
        "redis_clients_other": None if clients is None else clients["other_clients"],
    }
    expected = {
        "dns_control_resolves": True,
        **{f"{service}_resolves": False for service in EX10_SERVICES},
        "redis_clients_same_db": 0,
    }
    if any(resolved[service] for service in EX10_SERVICES) or (clients and clients["same_db"]):
        return ex10_preflight.Check(name, _RESULT.FAIL, expected, observed, "ex10-services-active")
    if not control_ok or None in resolved.values() or clients is None:
        return ex10_preflight.Check(
            name, _RESULT.INCOMPLETE, expected, observed, "ex10-services-not-demonstrated"
        )
    return ex10_preflight.Check(name, _RESULT.PASS, expected, observed)


def _evidence_facts(root: Path) -> dict[str, int]:
    """Runs EX-10 sin sellar y transacciones de custodia abiertas. Sólo `lstat` y listados:
    ningún archivo de evidencia se abre ni se escribe."""
    open_runs = runs = transactions = 0
    runs_root = root / "runs"
    if os.path.lexists(runs_root):
        with os.scandir(runs_root) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                run_root = Path(entry.path)
                if os.path.lexists(run_root / "metadata" / ex10_load.SCENARIO_ID):
                    runs += 1
                    open_runs += not os.path.lexists(run_root / ".s11-finalized")
    for name in ("custody-transactions", "finalization-transactions"):
        directory = root / name
        if os.path.lexists(directory):
            with os.scandir(directory) as entries:
                transactions += sum(1 for _ in entries)
    return {"ex10_runs": runs, "ex10_open_runs": open_runs, "open_transactions": transactions}


class _Guards:
    """Guardas previas a cualquier escritura: corren dentro de `SideEffectGuard`."""

    def __init__(self, options: PrepareOptions, probes: PrepareProbes):
        self.options = options
        self.probes = probes
        self.guard = _side_effect_guard(options)
        # El runner del preflight aporta los checks congelados de sus cuatro primeros pasos.
        self.runner = ex10_preflight._Runner(
            ex10_preflight.PreflightOptions(options.expected_git_sha, options.source_root),
            probes.preflight,
            self.guard,
        )
        self.services: dict[str, Any] | None = None
        own = [name for name in PREPARE_PREREQUISITES if name != "guard_coverage"]
        if options.mode is Mode.INSPECT:
            own.remove("ex10_services_stopped")
        plan = dict(ex10_preflight.CHECK_PLAN)
        self.required = [name for step in GUARD_STEPS for name, _ in plan[step]] + own

    @property
    def results(self) -> dict[str, ex10_preflight.CheckResult]:
        return self.runner.results

    def add(self, check: ex10_preflight.Check) -> None:
        self.runner.add(
            check.check, check.result, check.expected, check.observed_sanitized, check.detail_code
        )

    def run(self) -> tuple[ex10_preflight.Check, ...]:
        plan = dict(ex10_preflight.CHECK_PLAN)
        with self.guard:
            for step in GUARD_STEPS:
                try:
                    getattr(self.runner, step)()
                except ex10_preflight.PreflightViolation as exc:
                    marker = f"{step}_aborted"
                    self.runner.add(marker, _RESULT.FAIL, detail=f"guard-{exc.kind}")
                    missing = [name for name, _ in plan[step] if not self.runner.emitted(name)]
                    self.runner.skip(*missing, cause=marker)
            for name, method in (
                ("prepare_running_source", self.prepare_running_source),
                ("database_schema_current", self.database_schema_current),
                ("redis_endpoint_exact", self.redis_endpoint_exact),
                ("cache_approved", self.cache_approved),
                ("storage_contents", self.storage_contents),
                ("evidence_idle", self.evidence_idle),
                ("ex10_services_stopped", self.services_stopped),
            ):
                self.checked(name, method)
        self.side_effects()
        self.coverage()
        return tuple(self.runner.checks)

    def checked(self, name: str, method: Callable[[], None]) -> None:
        cause = self._blocker(name)
        # En INSPECT los servicios vivos sólo se informan: no son una guarda.
        informational = self.options.mode is Mode.INSPECT and name == "ex10_services_stopped"
        if cause is not None:
            if not informational:
                self.runner.skip(name, cause=cause)
            return
        try:
            method()
        except ex10_preflight.PreflightViolation as exc:
            if not informational:
                self.runner.add(name, _RESULT.FAIL, detail=f"guard-{exc.kind}")

    def _blocker(self, name: str) -> str | None:
        for prerequisite in PREPARE_PREREQUISITES[name]:
            result = self.results.get(prerequisite)
            if result in _BLOCKING:
                return prerequisite
            if result is _RESULT.NOT_RUN:
                return self.runner.blockers.get(prerequisite, "unresolved")
            if result is None:
                return "unresolved"
        return None

    def prepare_running_source(self) -> None:
        name = "prepare_running_source"
        try:
            repo = self.probes.preflight.repository(self.options.source_root)
            untracked = repo.untracked(PREPARE_SOURCES)
        except ex10_preflight.RepositoryUnavailable as exc:
            self.runner.add(name, _RESULT.INCOMPLETE, detail=exc.code)
            return
        backend = Path(settings.BASE_DIR)
        mismatched = 0
        for relative in PREPARE_SOURCES:
            try:
                running = (backend / relative.removeprefix("backend/")).read_bytes()
            except OSError:
                running = None
            if running is None or repo.read(relative) != running:
                mismatched += 1
        ok = mismatched == 0 and untracked == 0
        self.runner.add(
            name,
            _RESULT.PASS if ok else _RESULT.FAIL,
            {"mismatched": 0, "untracked": 0},
            {"compared": len(PREPARE_SOURCES), "mismatched": mismatched, "untracked": untracked},
            None if ok else "prepare-source-differs",
        )

    def database_schema_current(self) -> None:
        name = "database_schema_current"
        try:
            pending = self.probes.pending_migrations()
        except DatabaseError:
            self.runner.add(name, _RESULT.INCOMPLETE, detail="database-unavailable")
            return
        # Migrar no es parte del contrato: una base sin migrar se rechaza, no se corrige.
        self.runner.add(
            name,
            _RESULT.PASS if pending == 0 else _RESULT.FAIL,
            {"pending_migrations": 0},
            {"pending_migrations": pending},
            None if pending == 0 else "database-not-migrated",
        )

    def redis_endpoint_exact(self) -> None:
        facts = ex10_worker_probe.endpoint_facts(settings.REDIS_URL)
        ok = facts["matches_protocol"] is True and facts["db"] == 11
        self.runner.add(
            "redis_endpoint_exact",
            _RESULT.PASS if ok else _RESULT.FAIL,
            {"db": 11, "matches_protocol": True},
            {"db": facts["db"], "matches_protocol": facts["matches_protocol"]},
            None if ok else "redis-url-not-protocol",
        )

    def cache_approved(self) -> None:
        # `cache.clear()` es un `FLUSHDB` de su propia base: tiene que ser la aprobada.
        config = dict(settings.CACHES.get("default", {}))
        backend = config.get("BACKEND")
        if backend == "django.core.cache.backends.redis.RedisCache":
            kind, ok = "redis", config.get("LOCATION") == settings.REDIS_URL
        elif backend == "django.core.cache.backends.locmem.LocMemCache":
            kind, ok = "locmem", True
        else:
            kind, ok = "other", False
        self.runner.add(
            "cache_approved",
            _RESULT.PASS if ok else _RESULT.FAIL,
            {"approved": True},
            {"backend": kind, "approved": ok},
            None if ok else "cache-not-approved",
        )

    def storage_contents(self) -> None:
        name = "storage_contents"
        try:
            # El mismo recorrido que usa `reset_ex10_state`: sin seguir enlaces ni leer objetos.
            scan = ex10_state._scan_objects(Path(runtime.isolation_config().object_root))
        except OSError:
            self.runner.add(name, _RESULT.INCOMPLETE, detail="storage-unreadable")
            return
        # Los objetos se borran en el reset; un symlink o una raíz sin marcar, nunca.
        ok = scan.marker and scan.symlinks == 0
        self.runner.add(
            name,
            _RESULT.PASS if ok else _RESULT.FAIL,
            {"marker": True, "symlinks": 0},
            {"marker": scan.marker, "objects": scan.objects, "symlinks": scan.symlinks},
            None if ok else ("storage-symlink" if scan.symlinks else "storage-not-initialized"),
        )

    def evidence_idle(self) -> None:
        name = "evidence_idle"
        try:
            facts = _evidence_facts(Path(settings.S11_ARTIFACT_ROOT))
        except OSError:
            self.runner.add(name, _RESULT.INCOMPLETE, detail="evidence-unreadable")
            return
        if facts["ex10_open_runs"]:
            detail = "ex10-run-open"
        elif facts["open_transactions"]:
            detail = "evidence-transaction-open"
        else:
            detail = None
        self.runner.add(
            name,
            _RESULT.FAIL if detail else _RESULT.PASS,
            {"ex10_open_runs": 0, "open_transactions": 0},
            facts,
            detail,
        )

    def services_stopped(self) -> None:
        check = services_check(self.probes)
        self.services = check.observed_sanitized
        if self.options.mode is Mode.PREPARE:
            self.add(check)

    def side_effects(self) -> None:
        blocked = self.guard.uncontained()
        self.runner.add(
            "guard_no_side_effects",
            _RESULT.FAIL if blocked else _RESULT.PASS,
            {"blocked_effects": 0},
            {"blocked_effects": len(blocked)},
            "side-effect-blocked" if blocked else None,
        )

    def coverage(self) -> None:
        missing = [name for name in self.required if name not in self.results]
        unjustified = unjustified_not_run(tuple(self.runner.checks))
        problems = [
            code
            for code, found in (
                ("unexpected-not-run", unjustified),
                ("missing-required-check", missing),
            )
            if found
        ]
        self.runner.add(
            "guard_coverage",
            _RESULT.INCOMPLETE if problems else _RESULT.PASS,
            {"unjustified_not_run": [], "missing_required": []},
            {"unjustified_not_run": list(unjustified), "missing_required": missing},
            ",".join(problems) or None,
        )


# --- Reset e inspección ------------------------------------------------------------------------


@contextlib.contextmanager
def _publish_blocked(attempts: list[str]) -> Iterator[None]:
    """Ninguna tarea sale durante el reset, tampoco en eager.

    `apply_async` (y con él `delay`) y `send_task` se sustituyen por un rechazo que queda
    anotado aunque quien publicaba se trague la excepción (un `on_commit` robusto, por
    ejemplo). `before_task_publish` sólo detecta: Celery captura y registra las excepciones
    de sus receptores, así que lanzar desde la señal no bloquearía nada.
    """

    def refuse(*args, **kwargs):
        attempts.append("task-publish")
        raise _PublishAttempted()

    def detect(**kwargs):
        attempts.append("before-task-publish")

    uid = f"ex10-prepare-{id(attempts)}"
    originals = {owner: owner.__dict__[name] for owner, name in _PUBLISHERS}
    before_task_publish.connect(detect, weak=False, dispatch_uid=uid)
    for owner, name in _PUBLISHERS:
        setattr(owner, name, refuse)
    try:
        yield
    finally:
        for owner, name in _PUBLISHERS:
            setattr(owner, name, originals[owner])
        before_task_publish.disconnect(dispatch_uid=uid)


_PUBLISHERS = ((Task, "apply_async"), (Celery, "send_task"))


_RESET_EXPECTED = {"strategy": ex10_state.RESET_STRATEGY, "fixture": "PREPARED"}


def _reset_check(result, detail, observed=None) -> ex10_preflight.Check:
    return ex10_preflight.Check("reset", result, _RESET_EXPECTED, observed, detail)


def _reset(
    probes: PrepareProbes, dataset: ex10_load.LoadDataset
) -> tuple[ex10_preflight.Check, ResetFlags, ex10_fixture.FixtureResult | None]:
    with probes.exclusive_lock() as acquired:
        if not acquired:
            return _reset_check(_RESULT.FAIL, "prepare-already-running"), ResetFlags(), None
        try:
            environment = probes.writable_environment()
        except ex10_state.Ex10StateError as exc:
            return _reset_check(_RESULT.FAIL, f"approval-{_code(exc.code)}"), ResetFlags(), None
        except (DatabaseError, RedisError, OSError):
            return _reset_check(_RESULT.INCOMPLETE, "approval-unavailable"), ResetFlags(), None
        try:
            return _reset_approved(probes, environment, dataset)
        finally:
            _close(environment.redis)


def _reset_approved(probes, environment, dataset):
    # Segunda comprobación, inmediatamente antes del primer paso destructivo.
    before = services_check(probes)
    if before.result is not _RESULT.PASS:
        return _reset_check(before.result, before.detail_code), ResetFlags(), None
    # `seed_catalogs` escribe en stdout aun con verbosity=0: stdout es sólo el JSON final.
    attempts: list[str] = []
    try:
        with (
            _publish_blocked(attempts),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = probes.reset(environment, restore_fixture=True, dataset=dataset)
    except _PublishAttempted:
        return _reset_check(_RESULT.FAIL, "task-publish-attempted"), ResetFlags.unknown(), None
    except ex10_state.Ex10StateError as exc:
        status = _RESULT.INCOMPLETE if exc.code in _INCOMPLETE_RESET_CODES else _RESULT.FAIL
        return _reset_check(status, f"reset-{_code(exc.code)}"), ResetFlags.unknown(), None
    except ex10_fixture.Ex10FixtureError as exc:
        return _reset_check(_RESULT.FAIL, f"fixture-{_code(exc.code)}"), ResetFlags.unknown(), None
    except (DatabaseError, RedisError, OSError, CommandError):
        # `flush` convierte los errores SQL en `CommandError`: igual que una base caída.
        return _reset_check(_RESULT.INCOMPLETE, "reset-interrupted"), ResetFlags.unknown(), None
    payload = result.to_json()
    flags = ResetFlags.from_steps(payload["steps"])
    fixture = result.fixture
    outcome = None if fixture is None else str(fixture.outcome)
    observed = {"strategy": payload["strategy"], "fixture": outcome}
    if attempts:
        # Bloqueado, pero alguien intentó publicar: el reset no fue el contrato aprobado.
        return _reset_check(_RESULT.FAIL, "task-publish-attempted", observed), flags, fixture
    after = services_check(probes)
    if after.result is not _RESULT.PASS:
        # Algo arrancó durante el reset (o ya no se puede afirmar que no): el broker ya no
        # demuestra nada.
        detail = "ex10-services-started" if after.result is _RESULT.FAIL else after.detail_code
        return _reset_check(after.result, detail, observed), flags, fixture
    if outcome != str(ex10_fixture.FixtureOutcome.PREPARED):
        return _reset_check(_RESULT.FAIL, "fixture-drift-detected", observed), flags, fixture
    if not flags.complete:
        return _reset_check(_RESULT.FAIL, "reset-steps-incomplete", observed), flags, fixture
    return _reset_check(_RESULT.PASS, None, observed), flags, fixture


def _final_inspection(
    options: PrepareOptions, probes: PrepareProbes, dataset: ex10_load.LoadDataset
) -> tuple[ex10_state.StateReport | None, tuple[ex10_preflight.Check, ...]]:
    """READY_STATE y broker, de nuevo bajo `SideEffectGuard` y con Redis de sólo lectura."""
    guard = _side_effect_guard(options)
    checks: list[ex10_preflight.Check] = []
    report = environment = None
    with guard:
        try:
            environment = probes.readonly_environment()
            report = probes.inspect_ready_state(environment, dataset=dataset)
            snapshot = probes.classify_broker(environment.redis)
        except ex10_state.Ex10StateError as exc:
            checks.append(
                ex10_preflight.Check(
                    "final_inspection", _RESULT.FAIL, detail_code=f"approval-{_code(exc.code)}"
                )
            )
        except (DatabaseError, RedisError, OSError):
            checks.append(
                ex10_preflight.Check(
                    "final_inspection",
                    _RESULT.INCOMPLETE,
                    detail_code="final-inspection-unavailable",
                )
            )
        except ex10_preflight.PreflightViolation as exc:
            checks.append(
                ex10_preflight.Check(
                    "final_inspection", _RESULT.FAIL, detail_code=f"guard-{exc.kind}"
                )
            )
        else:
            checks.append(_ready_check(report))
            checks.append(_broker_check(snapshot))
        finally:
            if environment is not None:
                _close(environment.redis)
    blocked = guard.uncontained()
    checks.append(
        ex10_preflight.Check(
            "final_no_side_effects",
            _RESULT.FAIL if blocked else _RESULT.PASS,
            {"blocked_effects": 0},
            {"blocked_effects": len(blocked)},
            "side-effect-blocked" if blocked else None,
        )
    )
    return report, tuple(checks)


def _ready_check(report: ex10_state.StateReport) -> ex10_preflight.Check:
    overall = _RESULT(str(report.overall))
    failing = sorted(c.check for c in report.checks if c.result is not ex10_state.CheckStatus.PASS)
    return ex10_preflight.Check(
        "ready_state",
        overall,
        {"state": str(ex10_state.StateName.READY_STATE), "overall": "PASS"},
        {"state": str(report.state), "overall": str(overall), "not_pass": failing},
        None if overall is _RESULT.PASS else "ready-state-not-pass",
    )


def _broker_check(snapshot: ex10_preflight.BrokerSnapshot) -> ex10_preflight.Check:
    dynamic_ok = snapshot.dynamic == 0 and snapshot.task_messages == 0
    classified = snapshot.unknown == 0 and not snapshot.unclassifiable
    if not dynamic_ok:
        result, detail = _RESULT.FAIL, "broker-dynamic-state"
    elif not classified:
        result = _RESULT.INCOMPLETE
        detail = ",".join(snapshot.unclassifiable or ("unknown-broker-keys",))
    else:
        result, detail = _RESULT.PASS, None
    return ex10_preflight.Check(
        "broker_state",
        result,
        {"dynamic": 0, "task_messages": 0, "unknown": 0},
        snapshot.observed(),
        detail,
    )


def run_prepare(options: PrepareOptions, probes: PrepareProbes | None = None) -> PrepareReport:
    """Una preparación (o inspección) completa; una sola a la vez por proceso."""
    if not _SHA1.fullmatch(options.expected_git_sha):
        raise PrepareError("expected-git-sha-format")
    if not options.source_root.is_dir():
        raise PrepareError("source-root-missing")
    if not _RUN_LOCK.acquire(blocking=False):
        raise PrepareBusy()
    try:
        probes = probes or PrepareProbes()
        guards = _Guards(options, probes)
        guard_checks = guards.run()
        reset, flags, fixture = None, ResetFlags(), None
        ready, final = None, ()
        dataset = guards.runner.dataset
        if aggregate(guard_checks) is _RESULT.PASS:
            if options.mode is Mode.PREPARE:
                reset, flags, fixture = _reset(probes, dataset)
            # También tras un reset fallido: el JSON dice en qué estado quedó.
            ready, final = _final_inspection(options, probes, dataset)
        return PrepareReport(
            mode=options.mode,
            guards=guard_checks,
            services=guards.services,
            reset=reset,
            flags=flags,
            fixture=fixture,
            ready_state=ready,
            final=final,
            expected_git_sha=options.expected_git_sha,
            git_sha=guards.runner.git_sha,
            checked_at=probes.clock(),
        )
    finally:
        _RUN_LOCK.release()
