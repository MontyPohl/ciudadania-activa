from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TraceContext:
    run_id: str
    scenario_id: str
    request_id: str


_TRACE: ContextVar[TraceContext | None] = ContextVar("s11_trace", default=None)


def current_trace() -> TraceContext | None:
    return _TRACE.get()


def set_trace(context: TraceContext) -> Token:
    return _TRACE.set(context)


def reset_trace(token: Token) -> None:
    _TRACE.reset(token)
