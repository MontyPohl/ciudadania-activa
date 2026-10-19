from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

from django.db import connection

from .constants import REQUEST_ID_HEADER, RUN_ID_HEADER
from .context import TraceContext, reset_trace, set_trace
from .events import RequestEventBuffer, activate_buffer, deactivate_buffer, record_event
from .runtime import enabled, valid_request_id, valid_run_id, valid_scenario_id


class HttpInstrumentationMiddleware:
    """Telemetría HTTP minimizada, activa sólo para requests S11 correlacionados.

    `http.finished` y `celery.queued` se capturan durante el request, en los mismos
    instantes de siempre, y se persisten al cerrar la respuesta, cuando el servidor ya
    escribió el último byte: su escritura queda fuera del tiempo HTTP medido (G0, MD-05).
    `response.close()` es la frontera aplicativa WSGI/Django congelada para el protocolo,
    no un acuse TCP del cliente.
    """

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
        buffer = RequestEventBuffer()
        token = set_trace(context)
        buffer_token = activate_buffer(buffer)
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

        response = None
        status_code = 500
        try:
            with connection.execute_wrapper(query_timer):
                response = self.get_response(request)
            status_code = response.status_code
        finally:
            finished = datetime.now(UTC)
            try:
                record_event(
                    run_id=run_id,
                    scenario_id=scenario_id,
                    stream="http",
                    defer=True,
                    event={
                        "event": "http.finished",
                        "request_id": request_id,
                        # Campo histórico: `request.path`, sin query string.
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
            finally:
                deactivate_buffer(buffer_token)
                reset_trace(token)
            if response is None:
                # Una excepción que escapa sin respuesta no deja ningún cierre que esperar.
                buffer.flush()
        _flush_on_close(response, buffer)
        return response


def _flush_on_close(response, buffer: RequestEventBuffer) -> None:
    """Persiste el buffer del request justo después del cierre original de la respuesta.

    El servidor WSGI llama a `close()` tras escribir el último byte, también en respuestas
    streaming o de archivo (Django enlaza `file_to_stream.close` a este `close`), y el
    cliente de pruebas lo emula. Se conserva el `close` previo y sus excepciones se
    propagan; `flush` es idempotente, así que un segundo `close()` no vuelve a escribir.
    Una respuesta que nunca se cierra deja sus eventos sin persistir: la reconciliación
    los ve ausentes.
    """
    original_close = response.close

    def close():
        try:
            original_close()
        finally:
            buffer.flush()

    response.close = close
