from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from django.db import connection

from .constants import REQUEST_ID_HEADER, RUN_ID_HEADER
from .context import TraceContext, reset_trace, set_trace
from .events import record_event
from .runtime import enabled, valid_request_id, valid_run_id, valid_scenario_id


class HttpInstrumentationMiddleware:
    """Telemetría HTTP minimizada, activa sólo para requests S11 correlacionados."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not enabled():
            return self.get_response(request)

        run_id = request.META.get(RUN_ID_HEADER, "")
        request_id = request.META.get(REQUEST_ID_HEADER, "")
        scenario_id = request.META.get("HTTP_X_S11_SCENARIO_ID", "")
        if not (
            valid_run_id(run_id) and valid_request_id(request_id) and valid_scenario_id(scenario_id)
        ):
            return self.get_response(request)

        context = TraceContext(run_id=run_id, scenario_id=scenario_id, request_id=request_id)
        token = set_trace(context)
        started_wall = datetime.now(UTC)
        started = perf_counter()
        query_seconds = 0.0

        def query_timer(execute, sql, params, many, query_context):
            nonlocal query_seconds
            query_started = perf_counter()
            try:
                return execute(sql, params, many, query_context)
            finally:
                query_seconds += perf_counter() - query_started

        status_code = 500
        try:
            with connection.execute_wrapper(query_timer):
                response = self.get_response(request)
            status_code = response.status_code
            return response
        finally:
            finished = datetime.now(UTC)
            record_event(
                run_id=run_id,
                scenario_id=scenario_id,
                stream="http",
                event={
                    "event": "http.finished",
                    "request_id": request_id,
                    "endpoint": request.path,
                    "method": request.method,
                    "started_at": started_wall.isoformat().replace("+00:00", "Z"),
                    "finished_at": finished.isoformat().replace("+00:00", "Z"),
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "db_duration_ms": round(query_seconds * 1000, 3),
                    "http_status": status_code,
                    "success": 200 <= status_code < 400,
                },
            )
            reset_trace(token)
