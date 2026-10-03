"""Reconciliación de EX-10 (fase 3A): lee sin escribir y compone la evidencia de G0.

Parte de los 50 registros de intento ya custodiados y lee, sin modificar nada, la base S11
aislada, la telemetría técnica del run (`http.jsonl` y `celery.jsonl`) y el estado del
broker. Con eso construye exactamente las estructuras que el evaluador puro de `ex10_load`
espera: `PersistedReportEvidence` por intento `HTTP_ACCEPTED`, `Ht104Evidence` y las
condiciones de validez del entorno.

No altera intentos, no repara la base, no corrige la telemetría y no fabrica eventos. Lo
que no se pudo inspeccionar queda `None`, que el evaluador trata como evidencia ausente
(`NOT_DEMONSTRATED`), nunca como cero. Una lectura inspeccionada y vacía, en cambio, es un
hecho (`()`, `0`). La única correlación entre un request y su `Report` es el `id` del cuerpo
201: el dominio no guarda el `X-S11-Request-ID`.

Nunca compara el fin de la tarea con el fin del HTTP: HT1-04 es estructural (G0).

El broker post-run se juzga con la semántica de la identidad del run: la histórica (broker
vacío, `broker:dynamic-residual`) o la de G0-A2 (quiescencia: el trabajo pendiente invalida y
los residuos esperados de un run correcto no), sin reinterpretar nunca un run anterior.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from . import ex10_load
from .canonical import sha256_bytes
from .ex10_load import (
    AsyncConfigEvidence,
    AsyncFacts,
    AttemptOutcome,
    AttemptRecord,
    CeleryEventEvidence,
    GeneratorRun,
    Ht104Evidence,
    LoadPlan,
    PersistedReportEvidence,
    ProcessRef,
    SuccessStatus,
)

RECONCILIATION_SCHEMA_VERSION = "s11c3-ex10-reconciliation-v1"
TELEMETRY_SCHEMA_VERSION = "s11-technical-event-v1"
HTTP_FINISHED = "http.finished"
CELERY_EVENTS = frozenset({"celery.queued", "celery.started", "celery.finished", "celery.failed"})
TERMINAL_CELERY_EVENTS = frozenset({"celery.finished", "celery.failed"})
#: `task_postrun` con estado RETRY también emite `celery.finished`, pero la tarea volverá a
#: ejecutarse (`ai.classify_report` reintenta): no es un estado terminal para el drain.
NON_TERMINAL_CELERY_STATES = frozenset({"retry"})
AUDIT_REPORT_CREATED = ex10_load.AUDIT_REPORT_CREATED
AUDIT_AI_CLASSIFIED = ex10_load.AUDIT_AI_CLASSIFIED
REWARD_REPORT_CREATED = "REPORT_CREATED"
#: Un `Report` que la reconciliación no puede atribuir a ningún intento del run.
UNATTRIBUTABLE = "unattributable"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
#: Un stream legible puede tener, como mucho, un evento por request y fase: 50 × 5 cabe
#: holgado. Más es contaminación o un bucle del aparato, no un run de EX-10.
MAX_STREAM_EVENTS = 5_000


class Ex10ReconcileError(RuntimeError):
    """La reconciliación no puede componerse. Nunca contiene valores del entorno."""


# --- Tiempo ----------------------------------------------------------------------------


def utc_ns(value: Any) -> int | None:
    """ISO-8601 UTC (`…Z`, fracción opcional) a nanosegundos; cualquier otra cosa, `None`."""
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.utcoffset() != timedelta(0):
        return None
    return (parsed - _EPOCH) // timedelta(microseconds=1) * 1_000


def datetime_ns(value: datetime | None) -> int | None:
    if value is None or value.tzinfo is None:
        return None
    return (value.astimezone(UTC) - _EPOCH) // timedelta(microseconds=1) * 1_000


# --- Telemetría ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stream:
    """Un stream técnico leído de una vez. `events is None`: no se pudo inspeccionar."""

    name: str
    events: tuple[Mapping[str, Any], ...] | None
    sha256: str | None
    problems: tuple[str, ...] = ()


def parse_stream(raw: bytes | None, *, name: str, run_id: str, readable: bool = True) -> Stream:
    """Valida cada línea contra el esquema técnico y el run. Un fallo invalida el stream.

    `raw is None` con `readable=True` significa que el archivo no existe: nadie escribió
    un evento, que es un hecho (cero eventos), no una falta de inspección.
    """
    if not readable:
        return Stream(name, None, None, (f"telemetry:{name}:unreadable",))
    if raw is None:
        return Stream(name, (), None)
    digest = sha256_bytes(raw)
    events: list[Mapping[str, Any]] = []
    problems: list[str] = []
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    else:
        problems.append(f"telemetry:{name}:unterminated")
    if len(lines) > MAX_STREAM_EVENTS:
        return Stream(name, None, digest, (f"telemetry:{name}:too-many-events",))
    for number, line in enumerate(lines, start=1):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            problems.append(f"telemetry:{name}:malformed:{number}")
            continue
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != TELEMETRY_SCHEMA_VERSION
            or value.get("run_id") != run_id
            or value.get("scenario_id") != ex10_load.SCENARIO_ID
            or not isinstance(value.get("event"), str)
        ):
            problems.append(f"telemetry:{name}:foreign-or-invalid:{number}")
            continue
        events.append(value)
    if problems:
        # Un stream con una sola línea dudosa no se usa: no se elige qué creer.
        return Stream(name, None, digest, tuple(problems))
    return Stream(name, tuple(events), digest)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _process(value: Any) -> ProcessRef | None:
    if not isinstance(value, Mapping):
        return None
    hostname, pid = value.get("hostname"), value.get("pid")
    if not isinstance(hostname, str) or not hostname or type(pid) is not int or pid <= 0:
        return None
    return ProcessRef(hostname, pid)


def celery_evidence(event: Mapping[str, Any]) -> CeleryEventEvidence:
    open_transaction = event.get("db_transaction_open")
    return CeleryEventEvidence(
        event=event["event"],
        request_id=_text(event.get("request_id")),
        task_id=_text(event.get("task_id")),
        task_name=_text(event.get("task_name")),
        process=_process(event.get("process")),
        enqueue_at_utc_ns=utc_ns(event.get("enqueue_at")),
        start_at_utc_ns=utc_ns(event.get("start_at")),
        finish_at_utc_ns=utc_ns(event.get("finish_at")),
        db_transaction_open=open_transaction if type(open_transaction) is bool else None,
        status=_text(event.get("status")),
        celery_state=_text(event.get("celery_state")),
    )


# --- Base de datos -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportRow:
    """Un `Report` de la base aislada y lo que G0 exige de él, sin datos personales."""

    report_id: str
    citizen_id: str | None
    status: str
    created_at_utc_ns: int | None
    media_hashes: tuple[str, ...]
    created_rewards: tuple[tuple[int, int, str], ...]
    created_audit_rows: int
    classified_audit_rows: int
    classification_models: tuple[str, ...]
    #: `(previous_status, new_status)` en orden de creación. Diagnóstico: G0 no lo decide.
    status_history: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    """Todos los `Report` de la base aislada: tras `READY_STATE` sólo puede haber los del run."""

    reports: Mapping[str, ReportRow]


def snapshot_database() -> DatabaseSnapshot:
    """Lee la base dentro de una transacción `READ ONLY`: ninguna escritura es posible.

    Lanza `DatabaseError` si la base no responde; quien llama lo convierte en evidencia no
    inspeccionada. Nunca lee coordenadas, descripciones, emails ni documentos.
    """
    from django.db import connection, transaction

    from apps.admin_app.models import AuditLog
    from apps.ai_client_app.models import AiClassification
    from apps.reports_app.models import Report, ReportMedia, ReportStatusHistory
    from apps.rewards_app.models import RewardTransaction

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
        reports = list(
            Report.objects.order_by("id").values_list("id", "citizen_id", "status", "created_at")
        )
        ids = [str(pk) for pk, *_ in reports]
        media: dict[str, list[str]] = {key: [] for key in ids}
        for report_id, file_hash in ReportMedia.objects.filter(report_id__in=ids).values_list(
            "report_id", "file_hash"
        ):
            media[str(report_id)].append(file_hash)
        rewards: dict[str, list[tuple[int, int, str]]] = {key: [] for key in ids}
        for report_id, impact, credit, status in RewardTransaction.objects.filter(
            report_id__in=ids, event_type=REWARD_REPORT_CREATED
        ).values_list("report_id", "impact_delta", "credit_delta", "status"):
            rewards[str(report_id)].append((impact, credit, status))
        audits: Counter[tuple[str, str]] = Counter()
        for action, payload in AuditLog.objects.filter(
            action__in=[AUDIT_REPORT_CREATED, AUDIT_AI_CLASSIFIED]
        ).values_list("action", "payload"):
            report_id = payload.get("report_id") if isinstance(payload, dict) else None
            audits[(action, str(report_id))] += 1
        models: dict[str, list[str]] = {key: [] for key in ids}
        for report_id, model_name in AiClassification.objects.filter(report_id__in=ids).values_list(
            "report_id", "model_name"
        ):
            models[str(report_id)].append(model_name)
        history: dict[str, list[tuple[str, str]]] = {key: [] for key in ids}
        for report_id, previous, new in (
            ReportStatusHistory.objects.filter(report_id__in=ids)
            .order_by("created_at", "id")
            .values_list("report_id", "previous_status", "new_status")
        ):
            history[str(report_id)].append((previous, new))
    rows = {}
    for pk, citizen_id, status, created_at in reports:
        key = str(pk)
        rows[key] = ReportRow(
            report_id=key,
            citizen_id=None if citizen_id is None else str(citizen_id),
            status=status,
            created_at_utc_ns=datetime_ns(created_at),
            media_hashes=tuple(sorted(media[key])),
            created_rewards=tuple(sorted(rewards[key])),
            created_audit_rows=audits[(AUDIT_REPORT_CREATED, key)],
            classified_audit_rows=audits[(AUDIT_AI_CLASSIFIED, key)],
            classification_models=tuple(sorted(models[key])),
            status_history=tuple(history[key]),
        )
    return DatabaseSnapshot(rows)


# --- Configuración asíncrona (CFG_ASYNC) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigObservation:
    """`CFG_ASYNC` observado en un instante: API (este proceso) y worker (probe)."""

    api_task_always_eager: bool | None
    broker_url: str | None
    worker_nodes: int | None
    worker_max_concurrency: int | None
    worker_task_always_eager: bool | None
    worker_fake_ai_enabled: bool | None
    worker_ai_adapter: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "api_task_always_eager": self.api_task_always_eager,
            "broker_url": self.broker_url,
            "worker_nodes": self.worker_nodes,
            "worker_max_concurrency": self.worker_max_concurrency,
            "worker_task_always_eager": self.worker_task_always_eager,
            "worker_fake_ai_enabled": self.worker_fake_ai_enabled,
            "worker_ai_adapter": self.worker_ai_adapter,
        }

    @classmethod
    def from_json(cls, value: Any) -> ConfigObservation | None:
        if not isinstance(value, Mapping) or set(value) != set(_CONFIG_FIELDS):
            return None
        return cls(**{name: value[name] for name in _CONFIG_FIELDS})


_CONFIG_FIELDS = (
    "api_task_always_eager",
    "broker_url",
    "worker_nodes",
    "worker_max_concurrency",
    "worker_task_always_eager",
    "worker_fake_ai_enabled",
    "worker_ai_adapter",
)


def broker_label(url: Any) -> str | None:
    """El broker del protocolo tal cual (es público); cualquier otro, sin su valor."""
    if url is None:
        return None
    return ex10_load.BROKER_URL if url == ex10_load.BROKER_URL else "not-protocol-broker"


def async_config(
    before: ConfigObservation | None, after: ConfigObservation | None
) -> tuple[AsyncConfigEvidence | None, list[str]]:
    """`AsyncConfigEvidence` de G0 con el antes (tras START) y el después (tras el drain)."""
    if before is None:
        return None, ["cfg-async:before-missing"]
    problems = []
    if after is None:
        problems.append("cfg-async:after-missing")
    elif (
        after.api_task_always_eager,
        after.broker_url,
        after.worker_task_always_eager,
        after.worker_fake_ai_enabled,
        after.worker_ai_adapter,
    ) != (
        before.api_task_always_eager,
        before.broker_url,
        before.worker_task_always_eager,
        before.worker_fake_ai_enabled,
        before.worker_ai_adapter,
    ):
        problems.append("cfg-async:changed-during-run")
    if before.worker_ai_adapter != ex10_load.AI_MODEL_NAME:
        problems.append("cfg-async:worker-adapter")
    config = AsyncConfigEvidence(
        api_task_always_eager=before.api_task_always_eager,
        worker_task_always_eager=before.worker_task_always_eager,
        broker_url=before.broker_url,
        worker_nodes_before=before.worker_nodes,
        worker_max_concurrency_before=before.worker_max_concurrency,
        worker_nodes_after=None if after is None else after.worker_nodes,
        worker_max_concurrency_after=None if after is None else after.worker_max_concurrency,
        worker_fake_ai_enabled=before.worker_fake_ai_enabled,
    )
    return config, problems


# --- Reconciliación pura ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reconciliation:
    persisted: Mapping[str, PersistedReportEvidence] | None
    async_evidence: Ht104Evidence | None
    environment_invalidity: tuple[str, ...]
    attributed_reports: Mapping[str, str]
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


def _accepted(run: GeneratorRun) -> list[AttemptRecord]:
    return [a for a in run.attempts if a.outcome is AttemptOutcome.HTTP_ACCEPTED]


def reconcile(
    plan: LoadPlan,
    run: GeneratorRun,
    *,
    database: DatabaseSnapshot | None,
    http: Stream,
    celery: Stream,
    config_before: ConfigObservation | None,
    config_after: ConfigObservation | None,
    broker_dynamic: int | None,
    post_run_broker: PostRunBroker | None = None,
    g0_a2: bool = False,
) -> Reconciliation:
    """Compone la evidencia de G0 sin tocar nada. Función pura sobre datos ya leídos.

    El broker post-run se evalúa con la semántica de la identidad del run: G0-A2
    (`g0_a2`, sobre `post_run_broker`) o la histórica (`broker_dynamic`), nunca otra.
    """
    if g0_a2:
        broker_quiet = None if post_run_broker is None else post_run_broker.quiescent
    else:
        broker_quiet = None if broker_dynamic is None else broker_dynamic == 0
    by_key = plan.by_key()
    alias_by_user = {item.dataset_user_id: item.user_alias for item in plan.items}
    invalidity: list[str] = [*http.problems, *celery.problems]
    accepted = _accepted(run)

    # Un evento del run con un `request_id` fuera del plan no lo emitió el generador: el
    # middleware sólo traza requests con los tres identificadores S11 válidos.
    planned = {item.request_id for item in plan.items}
    foreign = sum(
        1
        for parsed in (http, celery)
        for event in parsed.events or ()
        if event.get("request_id") not in planned
    )
    if foreign:
        invalidity.append(f"telemetry:foreign-request-events:{foreign}")

    # Un `report_id` compartido por dos intentos no se atribuye a ninguno.
    shared = {rid for rid, n in Counter(a.report_id for a in accepted).items() if n > 1}
    invalidity.extend(
        f"reconciliation:report-id-shared:{a.request_key}"
        for a in accepted
        if a.report_id in shared
    )

    # --- Base: `PersistedReportEvidence` y atribución de cada `Report` -----------------
    persisted: dict[str, PersistedReportEvidence] | None
    attributed: dict[str, str] = {}
    if database is None:
        persisted = None
        invalidity.append("database:not-inspected")
    else:
        persisted = {}
        reports = database.reports
        claimed = {a.report_id: a.request_key for a in accepted if a.report_id not in shared}
        for report_id, key in claimed.items():
            if report_id in reports:
                attributed[report_id] = key
        # Un `Report` sin `id` de un 201 se atribuye por dueño e imagen (un duplicado o el
        # de un intento sin respuesta completa); si no encaja con ningún ítem del plan, es
        # contaminación del entorno y el run no demuestra nada.
        candidates = {
            (item.dataset_user_id, item.image_sha256): item.request_key for item in plan.items
        }
        for report_id, row in sorted(reports.items()):
            if report_id in attributed:
                continue
            match = [
                key
                for (user, image), key in candidates.items()
                if user == row.citizen_id and image in row.media_hashes
            ]
            attributed[report_id] = match[0] if len(match) == 1 else UNATTRIBUTABLE
        unattributable = sum(1 for value in attributed.values() if value == UNATTRIBUTABLE)
        if unattributable:
            invalidity.append(f"reconciliation:unattributable-reports:{unattributable}")
        for attempt in accepted:
            if attempt.report_id in shared:
                continue
            row = reports.get(attempt.report_id)
            # Filas del mismo request: la del 201 y cualquier duplicado por dueño e imagen.
            rows = sum(1 for value in attributed.values() if value == attempt.request_key)
            if row is None:
                persisted[attempt.request_key] = PersistedReportEvidence(
                    request_key=attempt.request_key,
                    report_id=attempt.report_id,
                    report_rows=rows,
                    owner_alias="",
                    media_file_hashes=(),
                    report_created_rewards=(),
                    report_created_audit_rows=0,
                )
                continue
            persisted[attempt.request_key] = PersistedReportEvidence(
                request_key=attempt.request_key,
                report_id=row.report_id,
                report_rows=rows,
                owner_alias=alias_by_user.get(row.citizen_id or "", ""),
                media_file_hashes=row.media_hashes,
                report_created_rewards=row.created_rewards,
                report_created_audit_rows=row.created_audit_rows,
            )

    # --- Telemetría: `Ht104Evidence` -------------------------------------------------------
    async_evidence: Ht104Evidence | None = None
    if celery.events is None:
        # Sin celery.jsonl legible no hay evidencia estructural: nunca un FAIL por eso.
        invalidity.append("ht1-04:celery-telemetry-not-inspected")
    else:
        http_by_request: dict[str, list[Mapping[str, Any]]] = {}
        if http.events is not None:
            for event in http.events:
                if event.get("event") == HTTP_FINISHED and _text(event.get("request_id")):
                    http_by_request.setdefault(event["request_id"], []).append(event)
        facts: dict[str, AsyncFacts] = {}
        for attempt in accepted:
            request_id = by_key[attempt.request_key].request_id
            row = None if database is None else database.reports.get(attempt.report_id or "")
            finished = http_by_request.get(request_id, [])
            if len(finished) > 1:
                invalidity.append(f"telemetry:http-duplicate:{attempt.request_key}")
            event = finished[0] if len(finished) == 1 else None
            if http.events is None:
                http_finished_at, api_process = None, None
            else:
                http_finished_at = None if event is None else utc_ns(event.get("finished_at"))
                api_process = None if event is None else _process(event.get("process"))
            facts[request_id] = AsyncFacts(
                request_id=request_id,
                report_created_at_utc_ns=None if row is None else row.created_at_utc_ns,
                http_finished_at_utc_ns=http_finished_at,
                api_process=api_process,
                ai_classification_models=None if row is None else row.classification_models,
                final_status=None if row is None else row.status,
                ai_classified_audit_rows=None if row is None else row.classified_audit_rows,
                task_in_broker=False if broker_quiet else None,
                enqueue_failure_logged=None,
            )
        config, config_problems = async_config(config_before, config_after)
        invalidity.extend(config_problems)
        events = tuple(celery_evidence(e) for e in celery.events if e.get("event") in CELERY_EVENTS)
        async_evidence = Ht104Evidence(config=config, events=events, facts=facts)

    if g0_a2:
        if post_run_broker is None:
            invalidity.append("broker:not-inspected")
        else:
            invalidity.extend(post_run_broker_problems(post_run_broker, celery))
    elif broker_dynamic is None:
        invalidity.append("broker:not-inspected")
    elif broker_dynamic:
        # Semántica histórica (runs sin G0-A2): la regla de broker vacío del READY_STATE.
        invalidity.append("broker:dynamic-residual")
    accepted_keys = {a.request_key for a in accepted}

    def attribution(value: str) -> str:
        if value == UNATTRIBUTABLE:
            return UNATTRIBUTABLE
        return "accepted-attempt" if value in accepted_keys else "other-attempt"

    diagnostics = {
        "http_events": None if http.events is None else len(http.events),
        "celery_events": None if celery.events is None else len(celery.events),
        "database_reports": None if database is None else len(database.reports),
        "attributed_reports": dict(sorted(Counter(map(attribution, attributed.values())).items())),
    }
    return Reconciliation(
        persisted=persisted,
        async_evidence=async_evidence,
        environment_invalidity=tuple(dict.fromkeys(invalidity)),
        attributed_reports=attributed,
        diagnostics=diagnostics,
    )


# --- Broker post-run (G0-A2) ----------------------------------------------------------------

#: Códigos post-run de G0-A2, en el orden en que se informan. `broker:not-inspected` se
#: conserva; `broker:dynamic-residual` queda sólo para la semántica histórica.
POST_RUN_BROKER_CODES = (
    "broker:queues-not-empty",
    "broker:unacked",
    "broker:pending-task-messages",
    "broker:unexpected-result-key",
    "broker:task-failure",
    "broker:task-retry",
    "broker:unexpected-throttle-key",
    "broker:unknown-residual",
    "broker:inclassifiable-residual",
)


@dataclass(frozen=True, slots=True)
class PostRunBroker:
    """El broker tras el run, sólo con nombres de clave y conteos: nunca valores (G0-A2).

    Lo construye el probe del executor con el cliente de sólo lectura. Las claves esperadas
    (throttle de los diez actores, caché de la aplicación) las deriva con la API de cache
    real; aquí sólo se comparan por igualdad exacta, nunca por sufijo o prefijo.
    """

    #: Colas y colas de prioridad de Kombu presentes (una lista existe sólo con mensajes).
    queue_keys: int
    unacked: int
    unacked_index: int
    #: Mensajes de tarea en el control plane, según el clasificador comprometido.
    task_messages: int
    #: Lo que el clasificador comprometido no pudo clasificar.
    unclassifiable: tuple[str, ...]
    #: Task IDs de las claves de resultado bien formadas.
    result_task_ids: tuple[str, ...]
    #: Claves con prefijo de resultado y sufijo que no es un task ID.
    malformed_result_keys: int
    #: Claves con el prefijo de throttle de la cache, de cualquier scope.
    throttle_keys: tuple[str, ...]
    expected_throttle_keys: tuple[str, ...]
    #: Claves de ninguna clase conocida (incluida, si existe, la caché adjudicada).
    unknown_keys: tuple[str, ...]
    expected_cache_key: str

    @property
    def quiescent(self) -> bool:
        """Sin trabajo pendiente en el broker: ni colas, ni entregas, ni mensajes de tarea."""
        return not (self.queue_keys or self.unacked or self.unacked_index or self.task_messages)


def post_run_broker_problems(broker: PostRunBroker, celery: Stream) -> list[str]:
    """Invalidez post-run del broker según G0-A2 (función pura, sin valores ni escrituras).

    El trabajo pendiente invalida. Los residuos esperados de un run correcto no: claves de
    resultado de tareas del run cuyo resultado la telemetría reconcilia (presentes, en parte
    o expiradas), throttle de los diez actores, bindings estáticos, control plane sin
    mensajes y la caché adjudicada. Un fallo o un reintento que la telemetría sí registra no
    es invalidez: lo decide HT1-04 (G0); aquí sólo cuenta el residuo que no reconcilia.
    """
    problems: set[str] = set()
    if broker.queue_keys:
        problems.add("broker:queues-not-empty")
    if broker.unacked or broker.unacked_index:
        problems.add("broker:unacked")
    if broker.task_messages:
        problems.add("broker:pending-task-messages")
    if broker.unclassifiable or broker.malformed_result_keys:
        problems.add("broker:inclassifiable-residual")
    if any(key != broker.expected_cache_key for key in broker.unknown_keys):
        problems.add("broker:unknown-residual")
    expected_throttle = set(broker.expected_throttle_keys)
    if any(key not in expected_throttle for key in broker.throttle_keys):
        problems.add("broker:unexpected-throttle-key")

    events = [e for e in celery.events or () if e.get("event") in CELERY_EVENTS]
    run_tasks = {e["task_id"] for e in events if _text(e.get("task_id"))}
    terminal = {e["task_id"] for e in events if _text(e.get("task_id")) and is_terminal_event(e)}
    for task_id in broker.result_task_ids:
        if task_id not in run_tasks:
            # De otro run, de una tarea desconocida o sin telemetría legible que la atribuya.
            problems.add("broker:unexpected-result-key")
        elif task_id not in terminal:
            # El broker guarda un desenlace que la telemetría del run no reconcilia.
            problems.add("broker:task-failure")
    retried = {
        e["task_id"]
        for e in events
        if e.get("event") == "celery.finished"
        and e.get("celery_state") in NON_TERMINAL_CELERY_STATES
        and _text(e.get("task_id"))
    }
    if retried - terminal:
        # Un reintento sin desenlace: trabajo que sigue pendiente después del drain.
        problems.add("broker:task-retry")
    return [code for code in POST_RUN_BROKER_CODES if code in problems]


# --- Registro canónico por intento ----------------------------------------------------------


def percentile_member(attempt: AttemptRecord) -> bool:
    """El mismo predicado que `ex10_load.latency_set`: todo 201 leído por completo."""
    return (
        attempt.http_status == 201
        and attempt.outcome in {AttemptOutcome.HTTP_ACCEPTED, AttemptOutcome.CONTRACT_VIOLATION}
        and attempt.duration_ns is not None
    )


def attempt_rows(
    plan: LoadPlan, run: GeneratorRun, successes: Sequence[Any]
) -> list[dict[str, Any]]:
    """Los 50 registros canónicos de §12: sin JWT, cuerpo, imagen, coordenadas ni PII."""
    status = {s.request_key: s.status for s in successes}
    by_key = plan.by_key()
    rows = []
    for attempt in run.attempts:
        item = by_key[attempt.request_key]
        reconciled = status.get(attempt.request_key)
        complete = attempt.outcome in {
            AttemptOutcome.HTTP_ACCEPTED,
            AttemptOutcome.CONTRACT_VIOLATION,
            AttemptOutcome.HTTP_REJECTED,
        }
        rows.append(
            {
                "attempt_index": item.index,
                "vu_id": attempt.vu,
                "vu_sequence": attempt.sequence_for_user,
                "request_key": attempt.request_key,
                "request_id": attempt.request_id,
                "t_start_monotonic_ns": attempt.t_start_ns,
                "t_end_monotonic_ns": attempt.t_end_ns,
                "started_wall_time_utc_ns": attempt.t_start_utc_ns,
                "finished_wall_time_utc_ns": attempt.t_end_utc_ns,
                "started_after_window": (
                    None if attempt.started_in_window is None else not attempt.started_in_window
                ),
                "completed_by_window_end": attempt.completed_in_window,
                "final_http_status": attempt.http_status if complete else None,
                "response_complete": complete,
                "response_contract_valid": attempt.outcome is AttemptOutcome.HTTP_ACCEPTED,
                "transport_result": str(attempt.outcome),
                "error_phase": attempt.error_phase,
                "error_class": attempt.error_class,
                "report_id": attempt.report_id,
                "latency_ms": (
                    None if attempt.duration_ns is None else round(attempt.duration_ns / 1e6, 6)
                ),
                "reconciliation_status": "NOT_APPLICABLE"
                if reconciled is None
                else str(reconciled),
                "throughput_success": reconciled is SuccessStatus.RECONCILED_SUCCESS
                and attempt.started_in_window is True
                and attempt.completed_in_window is True,
                "percentile_member": percentile_member(attempt),
            }
        )
    return rows


def ht1_04_rows(
    plan: LoadPlan,
    successes: Sequence[Any],
    evidence: Ht104Evidence | None,
    database: DatabaseSnapshot | None,
    attempts: Iterable[AttemptRecord],
) -> list[dict[str, Any]] | None:
    """La propiedad estructural de HT1-04 desglosada por éxito reconciliado (diagnóstico).

    Sólo describe lo observado; el resultado de HT1-04 lo decide `ex10_load.evaluate_ht1_04`.
    Sin identidades de proceso: sólo si coinciden o no. `None`: sin telemetría inspeccionada.
    """
    if evidence is None:
        return None
    by_key = plan.by_key()
    report_ids = {a.request_key: a.report_id for a in attempts}
    rows = []
    for success in successes:
        if success.status is not SuccessStatus.RECONCILED_SUCCESS:
            continue
        request_id = by_key[success.request_key].request_id
        facts = evidence.facts.get(request_id)
        queued = [
            e for e in evidence.events if e.event == "celery.queued" and e.request_id == request_id
        ]
        single = queued[0] if len(queued) == 1 else None
        task_id = None if single is None else single.task_id
        started = [
            e for e in evidence.events if e.event == "celery.started" and e.task_id == task_id
        ]
        finished = [
            e for e in evidence.events if e.event == "celery.finished" and e.task_id == task_id
        ]
        failed = [e for e in evidence.events if e.event == "celery.failed" and e.task_id == task_id]
        api = None if facts is None else facts.api_process
        worker = started[0].process if len(started) == 1 else None
        created = None if facts is None else facts.report_created_at_utc_ns
        enqueue = None if single is None else single.enqueue_at_utc_ns
        http_end = None if facts is None else facts.http_finished_at_utc_ns
        row = (
            None
            if database is None
            else database.reports.get(report_ids.get(success.request_key) or "")
        )
        models = None if facts is None else facts.ai_classification_models
        rows.append(
            {
                "request_key": success.request_key,
                "queued": len(queued),
                "queued_task_name_ok": None
                if single is None
                else single.task_name == ex10_load.TASK_NAME,
                "queued_process_is_api": None
                if single is None or single.process is None or api is None
                else single.process == api,
                "db_transaction_open": None if single is None else single.db_transaction_open,
                "created_at_relation": None
                if None in (created, enqueue, http_end)
                else created <= enqueue <= http_end,
                "started": None if task_id is None else len(started),
                "worker_process_present": None if task_id is None else worker is not None,
                "process_separation": None if worker is None or api is None else worker != api,
                "finished_success": None
                if task_id is None
                else len(finished) == 1
                and not failed
                and (finished[0].status, finished[0].celery_state) == ("finished", "success"),
                "finished": None if task_id is None else len(finished),
                "failed": None if task_id is None else len(failed),
                "classification_count": None if models is None else len(models),
                "fake_model": None
                if models is None
                else bool(models) and all(m == ex10_load.AI_MODEL_NAME for m in models),
                "final_status": None if facts is None else facts.final_status,
                "audit_ai_classified": None if facts is None else facts.ai_classified_audit_rows,
                "status_history": None if row is None else [list(h) for h in row.status_history],
            }
        )
    return rows


def _persisted_json(evidence: PersistedReportEvidence) -> dict[str, Any]:
    return {
        "request_key": evidence.request_key,
        "report_id": evidence.report_id,
        "report_rows": evidence.report_rows,
        "owner_alias": evidence.owner_alias,
        "media_file_hashes": None
        if evidence.media_file_hashes is None
        else list(evidence.media_file_hashes),
        "report_created_rewards": (
            None
            if evidence.report_created_rewards is None
            else [list(r) for r in evidence.report_created_rewards]
        ),
        "report_created_audit_rows": evidence.report_created_audit_rows,
    }


def _facts_json(facts: AsyncFacts) -> dict[str, Any]:
    process = facts.api_process
    return {
        "request_id": facts.request_id,
        "report_created_at_utc_ns": facts.report_created_at_utc_ns,
        "http_finished_at_utc_ns": facts.http_finished_at_utc_ns,
        "api_process": None
        if process is None
        else {"hostname": process.hostname, "pid": process.pid},
        "ai_classification_models": (
            None if facts.ai_classification_models is None else list(facts.ai_classification_models)
        ),
        "final_status": facts.final_status,
        "ai_classified_audit_rows": facts.ai_classified_audit_rows,
        "task_in_broker": facts.task_in_broker,
        "enqueue_failure_logged": facts.enqueue_failure_logged,
    }


def reconciliation_payload(
    plan: LoadPlan,
    run: GeneratorRun,
    reconciliation: Reconciliation,
    *,
    run_id: str,
    successes: Sequence[Any],
    http: Stream,
    celery: Stream,
    config_before: ConfigObservation | None,
    config_after: ConfigObservation | None,
    database: DatabaseSnapshot | None = None,
) -> dict[str, Any]:
    """Contenido de `ex10-reconciliation`: canónico, hashable y saneado."""
    persisted = reconciliation.persisted
    async_evidence = reconciliation.async_evidence
    return {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "protocol_id": ex10_load.PROTOCOL_ID,
        "methodology_version": ex10_load.METHODOLOGY_VERSION,
        "scenario_id": ex10_load.SCENARIO_ID,
        "dataset_id": ex10_load.DATASET_ID,
        "dataset_sha256": plan.dataset_sha256,
        "plan_sha256": plan.sha256,
        "run_id": run_id,
        "sources": {
            "http_events_sha256": http.sha256,
            "celery_events_sha256": celery.sha256,
            "http_events_inspected": http.events is not None,
            "celery_events_inspected": celery.events is not None,
            "database_inspected": persisted is not None,
        },
        "attempts": attempt_rows(plan, run, successes),
        "persisted": (
            None
            if persisted is None
            else {key: _persisted_json(value) for key, value in sorted(persisted.items())}
        ),
        "async_facts": (
            None
            if async_evidence is None
            else {k: _facts_json(v) for k, v in sorted(async_evidence.facts.items())}
        ),
        "ht1_04_attempts": ht1_04_rows(plan, successes, async_evidence, database, run.attempts),
        "config": {
            "before": None if config_before is None else config_before.to_json(),
            "after": None if config_after is None else config_after.to_json(),
        },
        "attributed_reports": dict(sorted(reconciliation.attributed_reports.items())),
        "environment_invalidity": list(reconciliation.environment_invalidity),
        "diagnostics": dict(reconciliation.diagnostics),
    }


def is_terminal_event(event: Mapping[str, Any]) -> bool:
    """`celery.failed`, o `celery.finished` salvo un RETRY (la tarea volverá a correr)."""
    name = event.get("event")
    if name == "celery.failed":
        return True
    return name == "celery.finished" and event.get("celery_state") not in NON_TERMINAL_CELERY_STATES


def terminal_request_ids(celery: Stream) -> tuple[set[str], set[str]] | None:
    """`(encoladas, terminadas)` por `request_id` a partir de celery.jsonl, o `None`.

    Una tarea es terminal con un evento terminal (`is_terminal_event`) de su `task_id`.
    Una tarea con reintentos sólo lo es tras su último intento.
    """
    if celery.events is None:
        return None
    queued: dict[str, str] = {}
    for event in celery.events:
        if event.get("event") == "celery.queued" and _text(event.get("task_id")):
            queued[event["task_id"]] = _text(event.get("request_id")) or ""
    finished_tasks = {
        e["task_id"] for e in celery.events if is_terminal_event(e) and e.get("task_id") in queued
    }
    # Por request: todas sus tareas encoladas deben haber terminado.
    by_request: dict[str, set[str]] = {}
    for task_id, request_id in queued.items():
        by_request.setdefault(request_id, set()).add(task_id)
    finished = {r for r, tasks in by_request.items() if tasks <= finished_tasks}
    return set(queued.values()), finished


def http_finished_request_ids(http: Stream) -> set[str] | None:
    if http.events is None:
        return None
    return {
        e["request_id"]
        for e in http.events
        if e.get("event") == HTTP_FINISHED and _text(e.get("request_id"))
    }


def responded_request_ids(plan: LoadPlan, attempts: Iterable[AttemptRecord]) -> set[str]:
    """Requests con una respuesta HTTP final leída: el servidor terminó su ciclo."""
    by_key = plan.by_key()
    return {
        by_key[a.request_key].request_id
        for a in attempts
        if a.outcome
        in {
            AttemptOutcome.HTTP_ACCEPTED,
            AttemptOutcome.CONTRACT_VIOLATION,
            AttemptOutcome.HTTP_REJECTED,
        }
    }
