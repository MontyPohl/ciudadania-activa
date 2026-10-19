from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from celery import signals
from django.db import connection

from .context import TraceContext, current_trace, reset_trace, set_trace
from .events import record_event
from .runtime import enabled, valid_request_id, valid_run_id, valid_scenario_id

_TASK_CONTEXT: dict[str, tuple[TraceContext, object, float, datetime]] = {}


@signals.before_task_publish.connect(weak=False)
def before_task_publish(sender=None, headers=None, **kwargs):
    if not enabled() or headers is None:
        return
    trace = current_trace()
    if trace is None:
        return
    headers["s11_run_id"] = trace.run_id
    headers["s11_scenario_id"] = trace.scenario_id
    headers["s11_request_id"] = trace.request_id
    # Dentro de un request S11 se persiste al cerrar la respuesta; fuera, en el acto.
    record_event(
        run_id=trace.run_id,
        scenario_id=trace.scenario_id,
        stream="celery",
        defer=True,
        event={
            "event": "celery.queued",
            "request_id": trace.request_id,
            "task_id": headers.get("id"),
            "task_name": sender,
            "enqueue_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "status": "queued",
            "db_transaction_open": _db_transaction_open(),
        },
    )


def _db_transaction_open() -> bool | None:
    """`in_atomic_block ∨ ¬autocommit` de la conexión por defecto al publicar (G0, MD-05).

    `None` si no puede leerse: la evidencia queda incompleta y nunca se supone cerrada.
    """
    try:
        return bool(connection.in_atomic_block or not connection.get_autocommit())
    except Exception:
        return None


# Los eventos del worker se persisten en el acto, también en eager. Eso es compatibilidad
# técnica, no asincronía: `process` es siempre el proceso real, que en eager es el del API.
# El preflight oficial rechaza eager (CFG_ASYNC de HT1-04).
@signals.task_prerun.connect(weak=False)
def task_prerun(task_id=None, task=None, **kwargs):
    if not enabled() or task is None or not _valid_headers(task.request):
        return
    trace = TraceContext(
        run_id=task.request.get("s11_run_id"),
        scenario_id=task.request.get("s11_scenario_id"),
        request_id=task.request.get("s11_request_id"),
    )
    token = set_trace(trace)
    started_wall = datetime.now(UTC)
    _TASK_CONTEXT[str(task_id)] = (trace, token, perf_counter(), started_wall)
    record_event(
        run_id=trace.run_id,
        scenario_id=trace.scenario_id,
        stream="celery",
        event={
            "event": "celery.started",
            "request_id": trace.request_id,
            "task_id": str(task_id),
            "task_name": task.name,
            "start_at": started_wall.isoformat().replace("+00:00", "Z"),
            "status": "started",
        },
    )


@signals.task_postrun.connect(weak=False)
def task_postrun(task_id=None, task=None, state=None, **kwargs):
    _finish(task_id=task_id, task=task, state=(state or "finished").lower())


@signals.task_failure.connect(weak=False)
def task_failure(task_id=None, sender=None, **kwargs):
    _finish(task_id=task_id, task=sender, state="failed")


def _finish(*, task_id, task, state: str) -> None:
    stored = _TASK_CONTEXT.pop(str(task_id), None)
    if stored is None:
        return
    trace, token, started, started_wall = stored
    finished = datetime.now(UTC)
    celery_state = state.lower()
    status = "failed" if celery_state in {"failed", "failure", "revoked"} else "finished"
    record_event(
        run_id=trace.run_id,
        scenario_id=trace.scenario_id,
        stream="celery",
        event={
            "event": "celery.failed" if status == "failed" else "celery.finished",
            "request_id": trace.request_id,
            "task_id": str(task_id),
            "task_name": getattr(task, "name", None),
            "start_at": started_wall.isoformat().replace("+00:00", "Z"),
            "finish_at": finished.isoformat().replace("+00:00", "Z"),
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "status": status,
            "celery_state": celery_state,
        },
    )
    reset_trace(token)


def _valid_headers(request) -> bool:
    return (
        valid_run_id(request.get("s11_run_id", ""))
        and valid_scenario_id(request.get("s11_scenario_id", ""))
        and valid_request_id(request.get("s11_request_id", ""))
    )
