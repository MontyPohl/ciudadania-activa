from __future__ import annotations

import logging
import os
import re
import socket
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from . import runtime
from .redaction import redact

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "s11-technical-event-v1"
#: Marca estable del log de un evento que no pudo persistirse. La reconciliación lo
#: trata como evento ausente (G0, MD-05); no hay reintento ni evento sustituto.
PERSIST_FAILED = "S11_TELEMETRY_PERSIST_FAILED"
_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_LOG_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def process_identity() -> dict[str, Any] | None:
    """`{hostname, pid}` del proceso que ejecuta la llamada, o `None` si no es válido.

    Se lee en cada evento, en el proceso que lo observa: un hijo del worker tiene su
    propio `pid`. Nunca proviene del cliente HTTP.
    """
    hostname = socket.gethostname()
    pid = os.getpid()
    if not _HOSTNAME.fullmatch(hostname) or type(pid) is not int or pid <= 0:
        return None
    return {"hostname": hostname, "pid": pid}


class RequestEventBuffer:
    """Eventos técnicos de un request S11 pendientes hasta el cierre de la respuesta.

    Sólo guarda payloads ya redactados. `flush` persiste una única vez, en el orden de
    captura; lo que llegue después se persiste en el acto.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str, str, dict[str, Any]]] = []
        self._flushed = False

    def add(self, run_id: str, scenario_id: str, stream: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            if self._flushed:
                return False
            self._pending.append((run_id, scenario_id, stream, payload))
            return True

    def flush(self) -> None:
        with self._lock:
            if self._flushed:
                return
            self._flushed = True
            pending, self._pending = self._pending, []
        for run_id, scenario_id, stream, payload in pending:
            _persist(run_id=run_id, scenario_id=scenario_id, stream=stream, payload=payload)

    @property
    def flushed(self) -> bool:
        return self._flushed

    def __len__(self) -> int:
        return len(self._pending)

    def __repr__(self) -> str:
        return f"RequestEventBuffer(pending={len(self._pending)}, flushed={self._flushed})"


_BUFFER: ContextVar[RequestEventBuffer | None] = ContextVar("s11_event_buffer", default=None)


def active_buffer() -> RequestEventBuffer | None:
    return _BUFFER.get()


def activate_buffer(buffer: RequestEventBuffer) -> Token:
    return _BUFFER.set(buffer)


def deactivate_buffer(token: Token) -> None:
    _BUFFER.reset(token)


def record_event(
    *,
    run_id: str,
    scenario_id: str,
    stream: str,
    event: dict[str, Any],
    defer: bool = False,
) -> None:
    """Registra un evento técnico S11 con el proceso que lo observó.

    Con `defer=True` y un request S11 en curso, el evento queda en el buffer del request
    y se persiste al cerrar la respuesta, fuera del tiempo HTTP medido (G0, MD-05). Sin
    buffer, o sin `defer`, se persiste en el acto.

    `recorded_at` y los demás timestamps son el instante en que se capturó el hecho: se
    fijan aquí una sola vez y el flush no los recalcula (decisión del autor, fase 2A).
    """
    if not runtime.enabled():
        return
    payload = redact(
        {
            "schema_version": SCHEMA_VERSION,
            "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "run_id": run_id,
            "scenario_id": scenario_id,
            **event,
            "process": process_identity(),
        }
    )
    buffer = _BUFFER.get() if defer else None
    if buffer is not None and buffer.add(run_id, scenario_id, stream, payload):
        return
    _persist(run_id=run_id, scenario_id=scenario_id, stream=stream, payload=payload)


def _persist(*, run_id: str, scenario_id: str, stream: str, payload: dict[str, Any]) -> None:
    try:
        runtime.evidence_store().append_jsonl(
            run_id=run_id,
            scenario_id=scenario_id,
            stream=stream,
            payload=payload,
        )
    except Exception as exc:
        # La instrumentación nunca altera la regla funcional ni el status ya enviado. El
        # log no lleva traza, mensaje, ruta ni payload: sólo identificadores validados.
        logger.error(
            "%s event=%s stream=%s run_id=%s scenario_id=%s request_id=%s task_id=%s error=%s",
            PERSIST_FAILED,
            _log_value(payload.get("event")),
            _log_value(stream),
            _log_value(run_id),
            _log_value(scenario_id),
            _log_value(payload.get("request_id")),
            _log_value(payload.get("task_id")),
            type(exc).__name__,
        )


def _log_value(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value)
    return text if _LOG_VALUE.fullmatch(text) else "[INVALID]"
