"""EX-10 fase 3A: executor — emisión, transporte, drain, primer run válido, custodia y crash.

LOCAL TEST / NON-EXPERIMENTAL. Cada run va contra un backend simulado con sockets TCP reales
de la biblioteca estándar en 127.0.0.1 (nunca internet ni el stack S11), con una custodia
temporal bajo `.s11-test-artifacts`. Los `run_id` son sintéticos de esa custodia de test; el
modo OFFICIAL sólo se usa aquí para probar la regla del primer run válido sobre ella.
Los números entre paréntesis son los ítems de §47, §48 y §53–§57 de la fase 3A.
"""

from __future__ import annotations

import builtins
import fcntl
import io
import json
import os
import shutil
import signal
import socket
import stat
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError

from apps.experiments_app import ex10_executor as executor
from apps.experiments_app import ex10_load as ex10
from apps.experiments_app import ex10_worker_probe
from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.evidence import EvidenceError, EvidenceStore
from apps.experiments_app.management.commands import s11_ex10
from apps.experiments_app.runtime import valid_run_id
from apps.experiments_app.tests.ex10_executor_support import (
    MS,
    RUN_ID,
    SHA,
    T0,
    T0_UTC,
    Backend,
    FakeClock,
    FakeTokens,
    close_harness,
    expected_config,
    make_harness,
    make_store,
    preflight_report,
    remove_store,
    run_files,
    synthetic_run,
)

P = executor.Phase
OFFICIAL = executor.OFFICIAL_MODE
LOCAL = executor.LOCAL_TEST_MODE


@pytest.fixture(scope="module")
def dataset():
    return ex10.load_dataset()


@pytest.fixture(scope="module")
def plan(dataset):
    return ex10.build_plan(dataset)


@pytest.fixture
def h(request, dataset):
    harness = make_harness(request.node.name, dataset)
    yield harness
    close_harness(harness)


def refusal(call, *args, **kwargs) -> executor.Ex10Refusal:
    with pytest.raises(executor.Ex10Refusal) as info:
        call(*args, **kwargs)
    return info.value


def attempts_of(h, run_id=None):
    return h.payload(executor.ART_RAW, run_id)["attempts"]


def digest(h, run_id=None):
    return {k: sha256_bytes(v) for k, v in run_files(h.store, run_id or h.run_id).items()}


def vu_threads():
    return [t for t in threading.enumerate() if t.name.startswith("ex10-vu-") and t.is_alive()]


class Raising:
    """El transporte real, salvo para `key`: ahí lanza `error` en vez de enviar."""

    def __init__(self, inner, key, error):
        self.inner, self.key, self.error = inner, key, error

    def send(self, request, *, clock, wall_clock):
        if request.item.request_key == self.key:
            raise self.error
        return self.inner.send(request, clock=clock, wall_clock=wall_clock)


class JumpClock:
    """Monotónico real más un salto que el test controla (una ventana de 60 s, al instante)."""

    def __init__(self):
        self.offset = 0

    def __call__(self):
        return time.monotonic_ns() + self.offset


class Jumping:
    """Antes de enviar `key` adelanta el reloj 61 s: `key` y lo que siga empieza tarde."""

    def __init__(self, inner, clock, key):
        self.inner, self.clock, self.key = inner, clock, key

    def send(self, request, *, clock, wall_clock):
        if request.item.request_key == self.key:
            self.clock.offset += 61_000_000_000
        return self.inner.send(request, clock=clock, wall_clock=wall_clock)


class Die(BaseException):
    """Mata el hilo del VU: no es un error de red ni una excepción ordinaria."""


# --- §56 Transporte HTTP real contra el servidor de test (12–23) ------------------------------


@pytest.fixture
def wire(request, dataset, plan):
    """Peticiones precodificadas y un backend real; sin run, sin custodia de run."""
    store = make_store(request.node.name)
    backend = Backend(store, plan, delay=0.2)
    tokens = FakeTokens([user["alias"] for user in dataset.payload["users"]])
    slugs = {item.category_slug for item in plan.items}
    prepared = ex10.prepare_requests(
        dataset,
        plan,
        run_id=RUN_ID,
        tokens=tokens.mapping(),
        category_ids={s: "00000000-0000-4000-8000-%012d" % i for i, s in enumerate(sorted(slugs))},
    )
    yield backend, prepared
    backend.close()
    remove_store(store)


def send(backend, request, *, clock=time.monotonic_ns, connect=None):
    transport = ex10.StdlibHttpTransport(backend.address, connect=connect)
    exchange = transport.send(request, clock=clock, wall_clock=time.time_ns)
    return exchange, ex10.attempt_record(request.item, exchange, t0_ns=exchange.t_start_ns)


@pytest.mark.parametrize(
    ("mode", "status", "outcome", "error"),
    [
        ("201", 201, "HTTP_ACCEPTED", None),  # (18)
        ("100-201", 201, "HTTP_ACCEPTED", None),  # (16)
        ("100-500", 500, "HTTP_REJECTED", "http_5xx"),  # (17)
        ("500", 500, "HTTP_REJECTED", "http_5xx"),
        ("truncated", 201, "TRANSPORT_FAILED", "incomplete_read"),  # (19)
        ("reset", None, "TRANSPORT_FAILED", "connection_reset"),  # (22)
        ("close", None, "TRANSPORT_FAILED", "connection_reset"),
        ("malformed", None, "TRANSPORT_FAILED", "protocol_error"),  # (23)
        ("slow-headers", 201, "HTTP_ACCEPTED", None),
        ("slow-body", 201, "HTTP_ACCEPTED", None),
    ],
)
def test_real_socket_responses_are_classified(wire, plan, mode, status, outcome, error):
    backend, prepared = wire
    request = prepared[plan.items[0].request_key]
    backend.modes[request.item.request_key] = mode
    exchange, record = send(backend, request)
    assert (record.http_status, str(record.outcome), record.error_class) == (status, outcome, error)
    assert record.send_count == 1 and backend.accepted == 1
    if outcome == "TRANSPORT_FAILED":
        assert record.error_phase == "receive"
        assert not ex10.latency_set([record])  # (51) un 201 truncado nunca entra
    if mode.startswith("slow-"):
        # t_end tras el último byte: la espera del servidor está dentro de la latencia.
        assert record.duration_ns >= 200 * MS


def test_each_attempt_is_one_new_connection_with_the_frozen_head(wire, plan):
    # (12) una conexión TCP por intento, (13) Connection: close, (14) sin Expect.
    backend, prepared = wire
    for item in plan.items[:5]:
        _, record = send(backend, prepared[item.request_key])
        assert record.outcome is ex10.AttemptOutcome.HTTP_ACCEPTED
    assert backend.accepted == len(backend.requests) == 5
    for seen, item in zip(backend.requests, plan.items[:5], strict=True):
        headers = seen["headers"]
        assert seen["line"] == "POST /api/v1/reports/ HTTP/1.1"
        assert headers["connection"] == ["close"]
        assert "expect" not in headers and "cookie" not in headers
        assert headers["host"] == [ex10.HOST_HEADER]
        assert headers["x-s11-request-id"] == [item.request_id]
        request = prepared[item.request_key]
        assert int(headers["content-length"][0]) == request.body_length == seen["body"]


def test_bodies_are_deterministic_across_preparations(dataset, plan):
    # §35: mismo cuerpo multipart y Content-Length con otros tokens; cambia sólo la cabecera.
    categories = {i.category_slug: "00000000-0000-4000-8000-000000000001" for i in plan.items}
    aliases = [user["alias"] for user in dataset.payload["users"]]

    def bodies():
        prepared = ex10.prepare_requests(
            dataset,
            plan,
            run_id=RUN_ID,
            tokens=FakeTokens(aliases).mapping(),
            category_ids=categories,
        )
        return {k: r.wire[-r.body_length :] for k, r in prepared.items()}, prepared

    (first, a), (second, b) = bodies(), bodies()
    assert first == second
    assert all(a[k].wire != b[k].wire for k in a)  # el token va sólo en la cabecera


def test_t_start_is_taken_before_connect(wire, plan):
    backend, prepared = wire

    def slow_connect(address, timeout):
        time.sleep(0.05)
        return socket.create_connection(address, timeout=timeout)

    _, record = send(backend, prepared[plan.items[0].request_key], connect=slow_connect)
    assert record.outcome is ex10.AttemptOutcome.HTTP_ACCEPTED
    assert record.duration_ns >= 50 * MS


def test_connect_timeout_is_a_transport_failure_with_the_total_budget(wire, plan):
    # (20) El connect recibe el plazo total restante (≤ 60 s) y su timeout es un fallo válido.
    backend, prepared = wire
    budgets = []

    def timing_out(address, timeout):
        budgets.append(timeout)
        raise TimeoutError("connect")

    _, record = send(backend, prepared[plan.items[0].request_key], connect=timing_out)
    assert (str(record.outcome), record.error_class, record.error_phase) == (
        "TRANSPORT_FAILED",
        "timeout",
        "connect",
    )
    assert len(budgets) == 1 and 59.0 < budgets[0] <= 60.0
    assert backend.accepted == 0


class AlmostDeadline:
    """Tras la primera lectura (t_start), el reloj salta al final del plazo menos `left`."""

    def __init__(self, left_ns):
        self.calls = 0
        self.left_ns = left_ns

    def __call__(self):
        self.calls += 1
        jump = 0 if self.calls == 1 else ex10.REQUEST_TIMEOUT_NS - self.left_ns
        return time.monotonic_ns() + jump


@pytest.mark.parametrize(
    ("mode", "status"),
    [("silent", None), ("slow-headers", None), ("slow-body", 201)],
)
def test_the_sixty_second_budget_is_total_from_t_start(wire, plan, mode, status):
    # (15, 21) Con 100 ms del plazo de 60 s por delante, un servidor lento agota el total:
    # timeout en receive, también si la línea de estado ya había llegado.
    backend, prepared = wire
    request = prepared[plan.items[0].request_key]
    backend.modes[request.item.request_key] = mode
    _, record = send(backend, request, clock=AlmostDeadline(100 * MS))
    assert (str(record.outcome), record.error_class, record.error_phase) == (
        "TRANSPORT_FAILED",
        "timeout",
        "receive",
    )
    assert record.http_status == status
    assert (
        ex10.REQUEST_TIMEOUT_NS - 100 * MS
        <= record.duration_ns
        < ex10.REQUEST_TIMEOUT_NS + 5 * 10**9
    )


@pytest.mark.parametrize("mode", ["201", "500", "100-201", "truncated", "reset", "malformed"])
def test_transport_classes_are_stable_over_repetitions(wire, plan, mode):
    # §60: 100 iteraciones por clase crítica, cada una en una conexión nueva.
    backend, prepared = wire
    request = prepared[plan.items[0].request_key]
    backend.modes[request.item.request_key] = mode
    results = {send(backend, request)[1].error_class for _ in range(100)}
    assert len(results) == 1
    assert backend.accepted == 100


# --- §47 Executor de extremo a extremo (1–11, 24–30) ------------------------------------------


def test_a_complete_run_emits_exactly_the_frozen_plan(h):
    started = h.start()
    assert valid_run_id(started["run_id"]) and h.phase() is P.PREPARED
    result = h.execute()
    assert (result["attempts"], result["sent"], result["t0_published"]) == (50, 50, True)
    attempts = attempts_of(h)
    raw = h.payload(executor.ART_RAW)
    # (1) 10 VU, (2) 5 por VU, (3) 50 intentos en el orden del plan.
    assert sorted({a["vu"] for a in attempts}) == list(range(1, 11))
    assert [a["request_key"] for a in attempts] == [i.request_key for i in h.plan.items]
    t0 = raw["t0_ns"]
    for vu in range(1, 11):
        mine = [a for a in attempts if a["vu"] == vu]
        assert [a["sequence_for_user"] for a in mine] == [1, 2, 3, 4, 5]
        assert mine[0]["t_start_ns"] >= t0  # (6, 7) nadie sale antes de la barrera
        for previous, current in zip(mine, mine[1:], strict=False):
            # (4) lazo cerrado y (5) sin think time.
            assert current["t_start_ns"] >= previous["t_end_ns"]
            assert current["t_start_ns"] - previous["t_end_ns"] < 250 * MS
    assert raw["window_end_ns"] == t0 + 60_000_000_000  # (8)
    assert abs(raw["t0_utc_ns"] - time.time_ns()) < 120 * 10**9
    # (11) sin reintentos y (12) una conexión por intento.
    assert all(a["send_count"] == 1 for a in attempts)
    assert h.backend.accepted == len(h.backend.requests) == 50
    assert sorted(r["key"] for r in h.backend.requests) == sorted(h.plan.by_key())
    assert h.phase() is P.RECONCILING
    final = h.finalize()
    assert (final["decision"], final["lifecycle"], final["manifest_status"]) == (
        "PASS",
        "FINALIZED_PASS",
        "COMPLETED",
    )
    assert final["binding"] is False and final["custody_errors"] == []  # LOCAL_TEST no liga
    assert h.phase() is P.FINALIZED_PASS
    assert vu_threads() == []


def test_the_barrier_is_created_once_per_run(h, monkeypatch):
    # (6)
    created = []
    original = ex10.threading.Barrier

    def counting(*args, **kwargs):
        created.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(ex10.threading, "Barrier", counting)
    h.start()
    h.execute()
    assert len(created) == 1 and created[0][0] == 10


def test_late_starts_are_emitted_and_never_count(h):
    # (9, 10) Un salto de 61 s: lo que empieza después de window_end se envía igual.
    clock = JumpClock()
    trigger = h.plan.for_vu(1)[2].request_key
    h.set_probes(
        generator=lambda plan, prepared, transport: ex10.LoadGenerator(
            plan, prepared, transport, clock=clock
        ),
        transport=lambda address: Jumping(ex10.StdlibHttpTransport(address), clock, trigger),
    )
    h.start()
    h.execute()
    attempts = attempts_of(h)
    window_end = h.payload(executor.ART_RAW)["window_end_ns"]
    late = [a for a in attempts if a["t_start_ns"] >= window_end]
    assert trigger in {a["request_key"] for a in late}
    assert len(attempts) == 50 and all(a["send_count"] == 1 for a in attempts)
    assert h.backend.accepted == 50
    final = h.finalize()
    rows = {r["request_key"]: r for r in h.payload(executor.ART_RECONCILIATION)["attempts"]}
    assert all(rows[a["request_key"]]["throughput_success"] is False for a in late)
    assert all(rows[a["request_key"]]["started_after_window"] is True for a in late)
    assert final["decision"] == "FAIL" and final["reasons"] == []


def test_a_500_is_sent_once_and_fails_the_run(h):
    # (11) Un 500 no se reintenta; el run es un FAIL válido.
    key = h.plan.for_vu(4)[3].request_key
    h.backend.modes[key] = "500"
    h.start()
    h.execute()
    assert [r["key"] for r in h.backend.requests].count(key) == 1
    final = h.finalize()
    assert (final["decision"], final["lifecycle"]) == ("FAIL", "FINALIZED_FAIL")


@pytest.mark.parametrize("mode", ["reset", "truncated", "malformed", "100-500"])
def test_valid_request_failures_keep_the_run_valid(h, mode):
    key = h.plan.for_vu(7)[0].request_key
    h.backend.modes[key] = mode
    h.start()
    h.execute()
    assert [r["key"] for r in h.backend.requests].count(key) == 1
    assert len(attempts_of(h)) == 50 and h.backend.accepted == 50
    final = h.finalize()
    assert (final["decision"], final["reasons"]) == ("FAIL", [])


def test_an_internal_transport_exception_is_an_apparatus_failure(h):
    # (24) y §62: el mensaje de la excepción nunca llega a la evidencia.
    key = h.plan.for_vu(2)[1].request_key
    h.set_probes(
        transport=lambda address: Raising(
            ex10.StdlibHttpTransport(address), key, RuntimeError("do-not-leak-this-detail")
        )
    )
    h.start()
    h.execute()
    attempts = attempts_of(h)
    assert len(attempts) == 50 and h.backend.accepted == 49  # (29)
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED" and final["lifecycle"] == "NOT_DEMONSTRATED"
    assert f"apparatus:internal_exception:{key}" in final["reasons"]
    assert b"do-not-leak-this-detail" not in b"".join(run_files(h.store, h.run_id).values())


def test_a_dead_vu_thread_is_an_apparatus_failure(h):
    # (27) El VU muere: sus ítems restantes quedan NOT_SENT y los demás VU siguen.
    key = h.plan.for_vu(5)[1].request_key
    h.set_probes(transport=lambda address: Raising(ex10.StdlibHttpTransport(address), key, Die()))
    h.start()
    h.execute()
    attempts = {a["request_key"]: a for a in attempts_of(h)}
    assert len(attempts) == 50  # (29)
    rest = [i.request_key for i in h.plan.for_vu(5)[2:]]
    assert {attempts[k]["outcome"] for k in rest} == {"NOT_SENT"}
    assert h.backend.accepted == 50 - 4
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert "apparatus:thread_died:vu-05" in final["reasons"]
    assert vu_threads() == []


def barrier_breaker(**kwargs):
    """El VU 10 nunca llega a la barrera: la barrera se rompe antes de t0."""
    if kwargs["name"] == "ex10-vu-10":
        kwargs = {**kwargs, "target": lambda *args: None}
    return threading.Thread(**kwargs)


def test_a_broken_barrier_sends_nothing_and_allows_a_clean_abandon(h):
    # (28) Sin t0 ni envíos: el único final limpio es el abandono antes de t0.
    h.set_probes(
        generator=lambda plan, prepared, transport: ex10.LoadGenerator(
            plan, prepared, transport, barrier_timeout_s=0.3, thread_factory=barrier_breaker
        )
    )
    h.start()
    result = h.execute()
    assert (result["t0_published"], result["sent"], h.backend.accepted) == (False, 0, 0)
    assert len(attempts_of(h)) == 50  # (29)
    assert h.finalize(abandon_reason="barrier-broken")["lifecycle"] == "ABANDONED_PRE_T0"


def test_a_broken_barrier_without_abandon_is_not_demonstrated(h):
    h.set_probes(
        generator=lambda plan, prepared, transport: ex10.LoadGenerator(
            plan, prepared, transport, barrier_timeout_s=0.3, thread_factory=barrier_breaker
        )
    )
    h.start()
    h.execute()
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert "t0-missing" in final["reasons"]


def test_an_operator_interruption_before_t0_stops_without_sending(h):
    # (25)
    def interrupt(result):
        raise KeyboardInterrupt

    h.set_probes(issue_tokens=interrupt)
    h.start()
    result = h.execute()
    assert (result["phase"], result["reasons"]) == ("STOPPED_PRE_T0", ["operator-interrupted"])
    assert h.backend.accepted == 0 and h.phase() is P.STOPPED_PRE_T0
    aborted = h.payload(executor.ART_EMISSION_ABORTED)
    assert (aborted["pre_t0"], aborted["t0_published"], aborted["requests_sent"]) == (
        True,
        False,
        0,
    )
    assert refusal(h.execute).terminal  # nunca se reintenta la emisión
    final = h.finalize(abandon_reason="operator-stop")
    assert (final["decision"], final["lifecycle"]) == ("NOT_DEMONSTRATED", "ABANDONED_PRE_T0")


def test_an_operator_interruption_after_t0_is_not_demonstrated(h):
    # (26) SIGINT real a este proceso a mitad de la emisión: nada de abandono limpio.
    key = h.plan.for_vu(3)[1].request_key
    h.backend.hooks[key] = lambda: os.kill(os.getpid(), signal.SIGINT)
    h.start()
    result = h.execute()
    assert result["interrupted"] is True and result["t0_published"] is True
    attempts = attempts_of(h)
    assert len(attempts) == 50  # (29) todos los registros, con NOT_SENT si no se enviaron
    assert vu_threads() == []
    assert refusal(h.finalize, abandon_reason="operator-stop").reasons == (
        "phase:RECONCILING:abandon-after-t0-not-allowed",
    )
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert "operator-interrupted" in final["reasons"]
    assert "apparatus:operator_interrupted:run" in final["reasons"]


def test_tokens_are_released_and_never_reach_the_evidence(h):
    # (30) y §34.
    h.start()
    h.execute()
    h.finalize()
    assert len(h.tokens) == 1 and h.tokens[0].released
    assert_clean_evidence(h)


def assert_clean_evidence(h, run_id=None):
    """§54 (101–109): ni JWT, Authorization, Cookie, multipart, imagen, coordenadas ni env."""
    files = run_files(h.store, run_id or h.run_id)
    text = b"\n".join(files.values()).decode("utf-8", "replace")
    for value in h.secret_values():
        assert value not in text
    forbidden = ("Bearer", "Authorization", "Cookie", "Content-Disposition", "multipart/form",
                 "s11ex10", "iVBORw0KGgo", "\x89PNG", "Traceback")  # fmt: skip
    for marker in forbidden:
        assert marker not in text, marker
    for name, value in os.environ.items():
        if (
            any(t in name for t in ("URL", "SECRET", "PASSWORD", "KEY", "TOKEN"))
            and len(value) >= 8
        ):
            assert value not in text, name
    for name, content in files.items():
        if name.endswith(".json"):
            payload = json.loads(content)
            assert content == canonical_json_bytes(payload), name  # (109)
            assert (
                ex10.sensitive_findings(payload, dataset=h.dataset, secrets=h.secret_values()) == []
            )


# --- Precondiciones de START y EXECUTE (§43, §44) ---------------------------------------------


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"git_sha": "b" * 40}, "preflight:git_sha"),
        ({"result": "FAIL"}, "preflight:result"),
        ({"gate": "NOT_READY"}, "preflight:gate"),
        ({"evidence_written": True}, "preflight:evidence_written"),
        ({"report_posts": 1}, "preflight:report_posts"),
        ({"checks": []}, "preflight:checks"),
        ({"checked_at": "2020-01-01T00:00:00Z"}, "preflight:stale"),
        ({"checked_at": "2999-01-01T00:00:00Z"}, "preflight:checked-at-future"),
        ({"checked_at": "yesterday"}, "preflight:checked-at"),
    ],
)
def test_start_requires_a_fresh_complete_preflight_pass_of_the_same_head(h, change, reason):
    h.set_probes(preflight=lambda options: {**preflight_report(git_sha=SHA), **change})
    rejected = refusal(h.start)
    assert reason in rejected.reasons and not rejected.terminal
    assert not (h.store.root / "runs").exists()  # nada escrito


FRESH = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
US = timedelta(microseconds=1)


@pytest.mark.parametrize(
    ("age", "problem"),
    [
        (timedelta(0), None),
        (timedelta(minutes=14, seconds=59), None),
        (timedelta(minutes=15), None),  # límite inclusivo: ≤ 15 min
        (timedelta(minutes=15) + US, "preflight:stale"),
        (-US, "preflight:checked-at-future"),  # tolerancia explícita hacia el futuro: 0
        (-timedelta(minutes=5), "preflight:checked-at-future"),
    ],
    ids=["same-instant", "fresh", "exact-limit", "stale", "future-1us", "future"],
)
def test_preflight_freshness_is_an_explicit_operational_bound(age, problem):
    # §14/§15: política de frescura propia, no la vida de un JWT (se emiten después).
    report = preflight_report(checked_at=FRESH - age)
    problems = executor.preflight_problems(report, git_sha=SHA, now=FRESH)
    assert problems == ([] if problem is None else [problem])
    assert executor.PREFLIGHT_MAX_AGE == timedelta(minutes=15)
    assert executor.PREFLIGHT_FUTURE_TOLERANCE == timedelta(0)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"git_sha": "b" * 40, "expected_git_sha": "b" * 40},
         {"preflight:git_sha", "preflight:expected_git_sha"}),
        ({"protocol_version": "s11-ex10-v2"}, {"preflight:protocol_version"}),
        ({"scenario_id": "EX-09"}, {"preflight:scenario_id"}),
        ({"gate": "NOT_READY"}, {"preflight:gate"}),
        ({"ready_for_execution": True}, {"preflight:ready_for_execution"}),
        ({"gate": "NOT_READY", "ready_for_execution": True},
         {"preflight:gate", "preflight:ready_for_execution"}),
    ],
    ids=["wrong-sha", "wrong-protocol", "wrong-scenario", "wrong-gate", "ready-true",
         "ready-true-does-not-replace-gate"],
)  # fmt: skip
def test_preflight_identity_and_gate_are_exact(change, expected):
    report = preflight_report(checked_at=FRESH, **change)
    assert set(executor.preflight_problems(report, git_sha=SHA, now=FRESH)) == expected


def test_emission_applies_the_same_freshness_bound(h):
    # La misma frontera al abrir la emisión, sobre el `checked_at` custodiado en START.
    now = {"value": FRESH}
    h.set_probes(
        now=lambda: now["value"],
        preflight=lambda options: preflight_report(git_sha=SHA, checked_at=FRESH),
    )
    h.start()
    now["value"] = FRESH + timedelta(minutes=15) + US
    rejected = refusal(h.execute)
    assert "preflight-stale" in rejected.reasons and h.backend.accepted == 0
    now["value"] = FRESH + timedelta(minutes=15)
    assert h.execute()["attempts"] == 50


def test_executor_sources_extend_the_preflight_without_duplicates():
    from apps.experiments_app import ex10_preflight

    assert len(set(executor.EXECUTOR_SOURCES)) == len(executor.EXECUTOR_SOURCES)
    assert executor.EXECUTOR_SOURCES[: len(ex10_preflight.RUNNING_SOURCES)] == (
        ex10_preflight.RUNNING_SOURCES
    )
    assert set(executor.EXECUTOR_SOURCES) - set(ex10_preflight.RUNNING_SOURCES) == {
        "backend/apps/experiments_app/ex10_prepare.py"
    }
    for relative in ("ex10_executor.py", "ex10_reconcile.py", "management/commands/s11_ex10.py"):
        assert f"backend/apps/experiments_app/{relative}" in ex10_preflight.RUNNING_SOURCES


class ClosingRedis:
    """El cliente de sólo lectura: cuenta cierres; `close` puede fallar como Redis."""

    def __init__(self, fail_close=False):
        self.closed = 0
        self.fail_close = fail_close
        self._client = self

    def close(self):
        self.closed += 1
        if self.fail_close:
            from redis.exceptions import ConnectionError as RedisConnectionError

            raise RedisConnectionError("close failed")


@pytest.mark.parametrize("fail_close", [False, True], ids=["close-ok", "close-fails"])
@pytest.mark.parametrize("probe", ["broker_dynamic", "inspect_ready_state"])
def test_readonly_probes_always_close_their_redis_client_and_only_that(
    monkeypatch, dataset, probe, fail_close
):
    # §16: `_close` sólo cierra el cliente, en `finally`, también si la sonda falla; un fallo
    # al cerrar no cambia el resultado ni el estado experimental.
    from apps.experiments_app import ex10_preflight, ex10_prepare, ex10_state

    redis = ClosingRedis(fail_close=fail_close)
    environment = SimpleNamespace(redis=redis)
    monkeypatch.setattr(ex10_prepare, "readonly_environment", lambda: environment)
    snapshot = SimpleNamespace(dynamic=0, task_messages=0, unclassifiable=())
    report = SimpleNamespace(to_json=lambda: {"state": "READY_STATE", "overall": "PASS"})
    monkeypatch.setattr(ex10_preflight, "classify_broker", lambda client: snapshot)
    monkeypatch.setattr(ex10_state, "inspect_ready_state", lambda env, dataset: report)
    call = (lambda: executor.broker_dynamic()) if probe == "broker_dynamic" else (
        lambda: executor.inspect_ready_state(dataset)
    )  # fmt: skip
    expected = 0 if probe == "broker_dynamic" else {"state": "READY_STATE", "overall": "PASS"}
    assert call() == expected and redis.closed == 1

    def boom(*args, **kwargs):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(ex10_preflight, "classify_broker", boom)
    monkeypatch.setattr(ex10_state, "inspect_ready_state", boom)
    with pytest.raises(RuntimeError):
        call()
    assert redis.closed == 2


def test_start_rejects_a_preflight_with_sensitive_content_before_writing(h):
    token = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlc2lnbmF0dXJl"
    report = preflight_report()
    report["checks"][0] = {**report["checks"][0], "observed_sanitized": token}
    h.set_probes(preflight=lambda options: report)
    assert "preflight:sensitive-content" in refusal(h.start).reasons
    assert not (h.store.root / "runs").exists()


def test_start_rejects_a_preflight_with_a_failed_or_missing_check(h):
    report = preflight_report()
    report["checks"][3] = {**report["checks"][3], "result": "FAIL"}
    h.set_probes(preflight=lambda options: report)
    assert "preflight:check-not-pass" in refusal(h.start).reasons
    missing = preflight_report()
    missing["checks"] = [c for c in missing["checks"] if c["check"] != "repository_running_source"]
    h.set_probes(preflight=lambda options: missing)
    assert "preflight:missing-required-check" in refusal(h.start).reasons


def test_start_refuses_a_real_provider(h, settings, monkeypatch):
    # §43: la comprobación real, no un doble.
    h.set_probes(provider=executor.provider_problems)
    settings.S11_FAKE_AI_ENABLED = False
    monkeypatch.setattr(ex10_worker_probe, "effective_ai_adapter", lambda: "gemini")
    assert {"fake-ai-disabled", "ai-adapter-not-s11-fake"} <= set(refusal(h.start).reasons)
    assert not (h.store.root / "runs").exists()


def test_start_refuses_executor_sources_that_differ_from_the_checkout(h):
    h.set_probes(sources=lambda root: ["executor-source-mismatch:x"])
    assert refusal(h.start).reasons == ("executor-source-mismatch:x",)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"evidence_mode": "PRODUCTION"}, "evidence-mode"),
        ({"git_sha": "HEAD"}, "git-sha-or-nonce-format"),
        ({"nonce": "short"}, "git-sha-or-nonce-format"),
        ({"nonce": "has spaces in it"}, "git-sha-or-nonce-format"),
    ],
)
def test_start_validates_its_arguments(h, kwargs, reason):
    assert refusal(h.start, **kwargs).reasons == (reason,)


@pytest.mark.parametrize(
    ("probe", "reason"),
    [
        ({"now": lambda: datetime.now(UTC) + timedelta(minutes=16)}, "preflight-stale"),
        ({"ready_state": lambda data: {"state": "DIRTY", "overall": "FAIL"}}, "ready-state"),
        ({"config": lambda: expected_config(api_task_always_eager=True)},
         "cfg-async:api_task_always_eager"),
        ({"config": lambda: expected_config(worker_ai_adapter="gemini")},
         "cfg-async:worker-adapter"),
        ({"fingerprint": lambda: "e" * 64}, "executor-code-changed-since-start"),
        ({"provider": lambda: ["fake-ai-disabled"]}, "fake-ai-disabled"),
    ],
)  # fmt: skip
def test_execute_rechecks_everything_before_the_point_of_no_return(h, probe, reason):
    h.start()
    h.set_probes(**probe)
    rejected = refusal(h.execute)
    assert reason in rejected.reasons and not rejected.terminal
    assert h.phase() is P.PREPARED and h.backend.accepted == 0 and h.tokens == []


# --- §48 Drain (31–41) ------------------------------------------------------------------------


def test_the_drain_deadline_is_max_window_end_last_attempt_plus_300_s(plan):
    # (31, 32, 33)
    early = synthetic_run(plan)
    assert executor.drain_deadline(early) == (T0 + 60 * 10**9 + 300 * 10**9, "window_end")
    late_key = plan.for_vu(1)[-1].request_key
    late = synthetic_run(
        plan,
        overrides={late_key: lambda i, s, e: ex10.Exchange(T0 + 70 * 10**9, T0 + 75 * 10**9,
                                                           T0_UTC, T0_UTC, http_status=500)},
    )  # fmt: skip
    assert executor.drain_deadline(late) == (T0 + 75 * 10**9 + 300 * 10**9, "last_attempt")
    assert executor.drain_deadline(replace(early, t0_ns=None)) is None


def unsettled():
    return executor.DrainObservation(True, queued_not_terminal=("load-request-01",))


def drain_with(plan, run, observe, clock, *, same_boot=True, wall=None):
    probes = executor.ExecutorProbes(monotonic_ns=clock, sleep=clock.sleep, wall_ns=wall or clock)
    return executor.run_drain(
        plan, run, observe=observe, probes=probes, same_boot=same_boot, t0_utc_ns=run.t0_utc_ns
    )


def test_the_drain_polls_every_second_exactly_without_backoff(plan):
    # (34, 35, 38) Del segundo 1 al 360 tras t0: 360 sondeos, 359 esperas de 1 s exacto.
    clock = FakeClock(start=T0 + 10**9)
    result = drain_with(plan, synthetic_run(plan), unsettled, clock)
    assert (result["outcome"], result["polls"]) == ("TIMEOUT", 360)
    assert clock.sleeps == [1.0] * 359
    assert result["pending_at_end"]["queued_not_terminal"] == ["load-request-01"]


def test_the_last_poll_never_waits_past_the_deadline(plan):
    key = plan.for_vu(1)[-1].request_key
    run = synthetic_run(
        plan,
        overrides={key: lambda i, s, e: ex10.Exchange(T0 + 60_000 * MS, T0 + 60_500 * MS,
                                                      T0_UTC, T0_UTC, http_status=500)},
    )  # fmt: skip
    clock = FakeClock(start=T0 + 10**9)
    result = drain_with(plan, run, unsettled, clock)
    assert clock.sleeps == [1.0] * 359 + [0.5]
    assert result["deadline"]["basis"] == "last_attempt" and result["outcome"] == "TIMEOUT"


def test_an_early_terminal_state_ends_the_drain(plan):
    # (37)
    observations = iter([unsettled(), unsettled(), executor.DrainObservation(True)])
    clock = FakeClock(start=T0)
    result = drain_with(plan, synthetic_run(plan), lambda: next(observations), clock)
    assert (result["outcome"], result["polls"], clock.sleeps) == ("COMPLETE", 3, [1.0, 1.0])


def test_another_boot_moves_the_deadline_to_the_wall_clock(plan):
    wall = FakeClock(start=T0_UTC + 10**9)
    other = FakeClock(start=0)
    probes = executor.ExecutorProbes(monotonic_ns=other, sleep=wall.sleep, wall_ns=wall)
    result = executor.run_drain(
        plan, synthetic_run(plan), observe=unsettled, probes=probes, same_boot=False,
        t0_utc_ns=T0_UTC,
    )  # fmt: skip
    assert result["deadline"]["clock"] == "wall" and result["polls"] == 360
    assert result["deadline"]["deadline_utc_ns"] == T0_UTC + 360 * 10**9


def test_a_run_without_t0_has_nothing_to_drain(plan):
    clock = FakeClock(start=T0)
    result = drain_with(plan, replace(synthetic_run(plan), t0_ns=None), unsettled, clock)
    assert (result["outcome"], result["polls"], clock.sleeps) == ("NO_T0", 0, [])


def test_the_drain_sends_nothing_and_mutates_nothing(h):
    # (36, 41) En cada sondeo: ni una conexión más ni un byte distinto en la custodia.
    seen = []
    database = h.probes.database

    def watching():
        seen.append((h.backend.accepted, tuple(sorted(digest(h).items()))))
        return database()

    h.backend.telemetry = False  # nada llega: el drain sondea hasta su plazo
    h.set_probes(database=watching)
    h.start()
    h.execute()
    assert len(seen) > 300
    assert {count for count, _ in seen} == {50}
    assert len({files for _, files in seen}) == 1
    drain = h.payload(executor.ART_DRAIN)
    assert (drain["outcome"], drain["requests_sent_during_drain"]) == ("TIMEOUT", 0)
    assert set(drain["pending_at_end"]["http_finished_missing"]) == set(h.plan.by_key())


def test_a_task_that_never_finishes_times_out_the_drain(h):
    # (38)
    h.backend.worker = False
    h.start()
    h.execute()
    drain = h.payload(executor.ART_DRAIN)
    assert (
        drain["outcome"] == "TIMEOUT" and len(drain["pending_at_end"]["queued_not_terminal"]) == 50
    )
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert "drain:timeout:task-not-terminal" in final["reasons"]


def test_an_unavailable_database_is_never_a_settled_drain(h):
    # (39)
    def down():
        raise DatabaseError("db down")

    h.set_probes(database=down)
    h.start()
    h.execute()
    drain = h.payload(executor.ART_DRAIN)
    assert (drain["outcome"], drain["final_poll_inspected"]) == ("TIMEOUT", False)
    assert drain["uninspected_polls"] == drain["polls"]
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert {"database:not-inspected", "drain:timeout:final-poll-uninspected"} <= set(
        final["reasons"]
    )


def test_a_task_left_in_retry_is_never_a_settled_drain(h):
    h.backend.retry = True
    h.start()
    h.execute()
    drain = h.payload(executor.ART_DRAIN)
    assert (
        drain["outcome"] == "TIMEOUT" and len(drain["pending_at_end"]["queued_not_terminal"]) == 50
    )
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert "drain:timeout:task-not-terminal" in final["reasons"]


def test_missing_telemetry_is_never_a_settled_drain(h):
    # (40)
    h.backend.telemetry = False
    h.start()
    h.execute()
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert "drain:timeout:http-finished-missing" in final["reasons"]


# --- §53 Primer run válido (94–100) -----------------------------------------------------------


def test_the_first_start_is_allowed(h):
    # (94)
    assert h.start(evidence_mode=OFFICIAL)["phase"] == "PREPARED"
    start = h.payload(executor.ART_START)
    assert (start["evidence_mode"], start["prior_ex10_runs"]) == (OFFICIAL, [])


@pytest.mark.parametrize(("mode", "decision"), [(None, "PASS"), ("500", "FAIL")])
def test_an_official_pass_or_fail_binds_ex10(h, mode, decision):
    # (96, 97) Ni un run oficial más, ni reconociendo el anterior.
    if mode:
        h.backend.modes[h.plan.items[0].request_key] = mode
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    final = h.finalize()
    assert (final["decision"], final["binding"]) == (decision, True)
    rejected = refusal(h.start, evidence_mode=OFFICIAL, acknowledged_not_demonstrated=[h.run_id])
    assert rejected.terminal
    assert f"binding-run-exists:{h.run_id}" in rejected.reasons
    assert len(list((h.store.root / "runs").iterdir())) == 1


def test_a_not_demonstrated_run_needs_an_explicit_acknowledgement(h):
    # (95) Nunca automáticamente: el operador reconoce el run_id NOT_DEMONSTRATED.
    h.set_probes(issue_tokens=lambda result: (_ for _ in ()).throw(KeyboardInterrupt))
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    h.finalize(abandon_reason="pre-t0-stop")
    previous = h.run_id
    rejected = refusal(h.start, evidence_mode=OFFICIAL)
    assert rejected.reasons == (f"not-demonstrated-unacknowledged:{previous}",)
    assert not rejected.terminal
    unknown = "s11-99999999-9999-5999-8999-999999999999"
    rejected = refusal(
        h.start, evidence_mode=OFFICIAL, acknowledged_not_demonstrated=[previous, unknown]
    )
    assert rejected.reasons == (f"acknowledged-unknown:{unknown}",)
    h.start(evidence_mode=OFFICIAL, acknowledged_not_demonstrated=[previous])
    assert h.run_id != previous
    assert h.payload(executor.ART_START)["acknowledged_not_demonstrated"] == [previous]


def test_an_active_run_blocks_any_other_start(h):
    # (99) Dos runs activos, nunca; tampoco uno oficial junto a uno de test.
    h.start(evidence_mode=LOCAL)
    for mode in (LOCAL, OFFICIAL):
        assert refusal(h.start, evidence_mode=mode).reasons == (f"active-run:{h.run_id}",)


def test_an_unverifiable_prior_run_blocks_the_start(h):
    h.start()
    h.execute()
    h.finalize()
    raw = next((h.state().run_root / "raw").rglob("*.json"))
    raw.write_bytes(raw.read_bytes().replace(b'"send_count":1', b'"send_count":0', 1))
    rejected = refusal(h.start, evidence_mode=OFFICIAL)
    assert rejected.reasons == (f"prior-run-unverifiable:{h.run_id}",)


def test_a_prior_run_cannot_be_hidden_by_removing_its_metadata(h):
    # §41: cualquier rastro de EX-10 en la custodia cuenta como run previo.
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    h.finalize()
    import shutil

    shutil.rmtree(h.state().run_root / "metadata" / "EX-10")
    rejected = refusal(h.start, evidence_mode=OFFICIAL)
    assert rejected.reasons == (f"prior-run-unverifiable:{h.run_id}",)


def test_a_run_id_is_never_reused(h):
    # (98, 121) Mismo instante y mismo nonce darían el mismo UUIDv5: se rechaza.
    moment = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    h.set_probes(
        now=lambda: moment,
        preflight=lambda options: preflight_report(git_sha=SHA, checked_at=moment),
    )
    h.start(nonce="nonce-fixed-0001")
    h.execute()
    h.finalize()
    rejected = refusal(h.start, nonce="nonce-fixed-0001")
    assert rejected.reasons == ("run-id-reused",) and rejected.terminal
    other = h.start(nonce="nonce-fixed-0002")["run_id"]
    assert other != h.run_ids[0]


def test_there_is_no_selective_rerun(h):
    # (100, 119) Ni la emisión entera ni una parte: execute no se repite en ninguna fase.
    h.backend.modes[h.plan.items[0].request_key] = "reset"
    h.start()
    h.execute()
    assert refusal(h.execute).terminal
    h.finalize()
    assert refusal(h.execute).reasons == ("run-finalized",)
    assert h.backend.accepted == 50


def test_the_binding_rule_is_stable(plan):
    # §60: la regla sobre runs previos sintéticos, 50 veces.
    prior = [
        executor.PriorRun("s11-a", OFFICIAL, True, "NOT_DEMONSTRATED", ()),
        executor.PriorRun("s11-b", LOCAL, True, "PASS", ()),
        executor.PriorRun("s11-c", OFFICIAL, True, "FAIL", ()),
    ]
    for _ in range(50):
        reasons, terminal = executor.binding_refusal(
            prior, evidence_mode=OFFICIAL, acknowledged=["s11-a"]
        )
        assert (reasons, terminal) == (["binding-run-exists:s11-c"], True)
        assert executor.binding_refusal(
            prior[:2], evidence_mode=OFFICIAL, acknowledged=["s11-a"]
        ) == (
            [],
            False,
        )
        assert executor.binding_refusal(prior, evidence_mode=LOCAL, acknowledged=[]) == ([], False)


# --- §54 Evidencia (101–113) ------------------------------------------------------------------


def test_every_artifact_is_canonical_sanitized_and_complete(h):
    # (101–109)
    h.start()
    h.execute()
    h.finalize()
    assert_clean_evidence(h)
    types = {record["artifact_type"] for record in h.state().records}
    assert types == {
        executor.ART_MANIFEST_INITIAL, executor.ART_PREFLIGHT, executor.ART_PLAN,
        executor.ART_START, executor.ART_EMISSION, executor.ART_ARMED, executor.ART_RAW,
        executor.ART_DRAIN,
        executor.ART_RECONCILIATION, executor.ART_CALCULATION, executor.ART_DECISION,
        "http-events", "celery-events", executor.ART_MANIFEST_FINAL,
    }  # fmt: skip
    assert h.store.verify(h.run_id) == []


def test_persist_refuses_coordinates_and_images_that_custody_would_accept(h, dataset):
    # La custodia sólo bloquea patrones de secreto; coordenadas e imágenes las para el executor.
    h.start()
    row = dataset.payload["requests"][0]
    for payload in (
        {"note": repr(float(row["latitude"]))},
        {"note": row["image"]["base64"][:200] + "..."},
        {"latitude": 1},
    ):
        with pytest.raises(executor.Ex10ExecutorError):
            executor._persist(
                h.store, h.run_id, executor.ART_DRAIN, payload, source="test", dataset=dataset
            )
    assert not h.state().has(executor.ART_DRAIN)


def test_the_plan_artifact_is_deterministic(h):
    # (110)
    h.start()
    first = h.state().record(executor.ART_PLAN)["sha256"]
    h.finalize(abandon_reason="test")
    h.start()
    assert h.state().record(executor.ART_PLAN)["sha256"] == first
    assert first == sha256_bytes(canonical_json_bytes(h.plan.to_json()))


def test_a_finalized_run_is_immutable(h):
    # (111, 112, 113)
    h.start()
    h.execute()
    final = h.finalize()
    decision = h.state().record(executor.ART_DECISION)
    assert decision["sha256"] == final["decision_sha256"]
    manifest = h.payload(executor.ART_MANIFEST_FINAL)
    referenced = {ref["sha256"] for ref in manifest["artifact_references"]}
    assert decision["sha256"] in referenced
    assert h.state().record(executor.ART_RAW)["sha256"] in referenced
    with pytest.raises(EvidenceError):
        h.store.append_jsonl(
            run_id=h.run_id, scenario_id="EX-10", stream="http", payload={"event": "x"}
        )
    with pytest.raises(EvidenceError):
        executor._persist(
            h.store, h.run_id, executor.ART_DECISION, {"decision": "FAIL"},
            source="test", dataset=h.dataset,
        )  # fmt: skip
    assert refusal(h.finalize).reasons == ("run-finalized",)
    assert executor.run_status(h.store, h.run_id)["decision"] == "PASS"


def test_tampering_after_the_seal_is_detected(h):
    h.start()
    h.execute()
    h.finalize()
    raw = next((h.state().run_root / "raw").rglob("*.json"))
    raw.write_bytes(raw.read_bytes().replace(b"HTTP_ACCEPTED", b"HTTP_REJECTED", 1))
    assert h.store.verify(h.run_id)


def test_reconcile_is_read_only_and_repeatable(h):
    # (117) Un «crash» durante reconcile no deja rastro: no escribe nada.
    h.start()
    h.execute()
    before = digest(h)
    results = [h.reconcile() for _ in range(3)]
    assert results[0] == results[1] == results[2]
    assert results[0]["provisional"] is True and results[0]["decision"] == "PASS"
    assert digest(h) == before and h.phase() is P.RECONCILING


def test_steps_on_an_unknown_run_create_nothing(h):
    missing = "s11-12345678-1234-5234-8234-123456789012"
    for step in (h.execute, h.drain, h.reconcile, h.finalize):
        rejected = refusal(step, missing)
        assert rejected.reasons == ("run-not-found",) and rejected.terminal
    assert executor.run_status(h.store, missing) == {"run_id": missing, "exists": False}
    assert not (h.store.root / "runs" / missing).exists()


def test_any_inspection_failure_at_finalize_is_not_demonstrated_not_a_crash(h):
    # Una sonda rota (de cualquier tipo) deja el run cerrable como NOT_DEMONSTRATED.
    h.start()
    h.execute()

    def broken():
        raise RuntimeError("postgres://user:secret@db/x")

    h.set_probes(database=broken, broker_dynamic=broken)
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert {"probe:database:RuntimeError", "probe:broker:RuntimeError"} <= set(final["reasons"])
    assert b"secret@db" not in b"".join(run_files(h.store, h.run_id).values())


def test_a_failed_config_probe_after_the_drain_is_not_demonstrated(h):
    calls = []

    def config():
        calls.append(1)
        if len(calls) > 1:
            raise OSError("control plane down")
        return expected_config()

    h.set_probes(config=config)
    h.start()
    h.execute()
    assert h.payload(executor.ART_DRAIN)["config_after"] is None
    final = h.finalize()
    assert final["decision"] == "NOT_DEMONSTRATED"
    assert "cfg-async:after-missing" in final["reasons"]


# --- §55 Crash y recuperación (114–121) -------------------------------------------------------


def test_a_crash_inside_start_leaves_a_run_that_only_abandon_closes(h, monkeypatch):
    original = executor._persist

    def dies_at_preflight(store, run_id, artifact_type, payload, **kwargs):
        if artifact_type == executor.ART_PREFLIGHT:
            raise SystemExit(137)
        return original(store, run_id, artifact_type, payload, **kwargs)

    monkeypatch.setattr(executor, "_persist", dies_at_preflight)
    with pytest.raises(SystemExit):
        h.start(nonce="nonce-crash-0001")
    monkeypatch.setattr(executor, "_persist", original)
    (run_id,) = (path.name for path in (h.store.root / "runs").iterdir())
    assert executor.phase_of(executor.load_state(h.store, run_id)) is P.CREATED
    assert refusal(h.start).reasons == (f"active-run:{run_id}",)
    assert refusal(h.execute, run_id).terminal
    final = h.finalize(run_id, abandon_reason="start-crashed")
    assert final["lifecycle"] == "ABANDONED_PRE_T0"
    assert h.start()["run_id"] != run_id


def test_a_crash_before_arming_is_provably_pre_t0(h):
    # (114) El proceso muere preparando tokens: marcador de emisión sin armar, nada enviado.
    def die(specs):
        raise SystemExit(137)

    h.set_probes(fixture_result=die)
    h.start()
    with pytest.raises(SystemExit):
        h.execute()
    assert h.phase() is P.STARTED and h.backend.accepted == 0
    assert refusal(h.execute).terminal
    assert refusal(h.finalize).reasons == ("phase:STARTED:executor-may-still-run",)
    final = h.finalize(abandon_reason="crash-before-arming")
    assert (final["decision"], final["lifecycle"]) == ("NOT_DEMONSTRATED", "ABANDONED_PRE_T0")


def test_a_crash_before_arming_can_also_be_closed_as_not_demonstrated(h):
    h.set_probes(fixture_result=lambda specs: (_ for _ in ()).throw(SystemExit(137)))
    h.start()
    with pytest.raises(SystemExit):
        h.execute()
    final = h.finalize(declare_executor_gone=True)
    assert final["reasons"] == ["emission-interrupted:before-arming"]


def test_an_abandon_while_the_executor_prepares_prevents_any_emission(h):
    # El operador abandona mientras el executor sigue vivo: armar exige el run abierto.
    def abandon_then_resolve():
        h.finalize(abandon_reason="operator-abandon")
        return h.backend.address

    h.set_probes(resolve_backend=abandon_then_resolve)
    h.start()
    rejected = refusal(h.execute)
    assert rejected.reasons == ("run-finalized",) and rejected.terminal
    assert h.backend.accepted == 0 and h.tokens[0].released
    assert executor.run_status(h.store, h.run_id)["phase"] == "ABANDONED_PRE_T0"


def test_an_interrupt_while_arming_is_a_clean_stop_before_t0(h, monkeypatch):
    # El SIGINT llega mientras se custodia el armado: se entrega al terminar el checkpoint,
    # antes de la barrera. Nada se envió: STOPPED_PRE_T0.
    original = executor._persist

    def signal_while_arming(store, run_id, artifact_type, payload, **kwargs):
        record = original(store, run_id, artifact_type, payload, **kwargs)
        if artifact_type == executor.ART_ARMED:
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)  # la señal queda registrada, no interrumpe el checkpoint
        return record

    monkeypatch.setattr(executor, "_persist", signal_while_arming)
    h.start()
    result = h.execute()
    assert (result["phase"], result["reasons"]) == ("STOPPED_PRE_T0", ["operator-interrupted"])
    assert h.state().has(executor.ART_ARMED) and h.phase() is P.STOPPED_PRE_T0
    assert h.backend.accepted == 0
    assert h.finalize(abandon_reason="operator-stop")["lifecycle"] == "ABANDONED_PRE_T0"


def test_a_crash_while_emitting_is_not_demonstrated_and_never_resumed(h):
    # (115, 119)
    class DyingGenerator:
        def __init__(self, plan, prepared, transport):
            self.inner = ex10.LoadGenerator(plan, prepared, transport)

        def run(self):
            self.inner.run()
            raise SystemExit(137)

    h.set_probes(generator=DyingGenerator)
    h.start()
    with pytest.raises(SystemExit):
        h.execute()
    assert h.phase() is P.EMITTING and h.backend.accepted == 50
    assert refusal(h.execute).terminal
    assert h.backend.accepted == 50  # (119) nada se reenvía
    assert refusal(h.finalize, abandon_reason="x").reasons == (
        "phase:EMITTING:abandon-after-t0-not-allowed",
    )
    assert refusal(h.finalize).reasons == ("phase:EMITTING:executor-may-still-run",)
    final = h.finalize(declare_executor_gone=True)
    assert (final["decision"], final["reasons"]) == (
        "NOT_DEMONSTRATED",
        ["emission-interrupted:raw-missing"],
    )


def test_a_crash_during_the_drain_is_recovered_by_observation_only(h):
    # (116, 120) El drain se retoma leyendo; lo ya custodiado no cambia.
    calls = []
    database = h.probes.database

    def dies_once():
        calls.append(1)
        if len(calls) == 1:
            raise SystemExit(137)
        return database()

    h.set_probes(database=dies_once)
    h.start()
    with pytest.raises(SystemExit):
        h.execute()
    assert h.phase() is P.DRAINING
    before = run_files(h.store, h.run_id)
    assert refusal(h.finalize).reasons == ("phase:DRAINING:executor-may-still-run",)
    drain = h.drain()
    assert drain["outcome"] == "COMPLETE" and drain["requests_sent_during_drain"] == 0
    assert_preserved(before, run_files(h.store, h.run_id))
    assert h.finalize()["decision"] == "PASS"
    assert h.backend.accepted == 50


def assert_preserved(before, after):
    """(120) Cada artefacto custodiado sigue byte a byte y su registro conserva el hash.

    El índice se reescribe al sellar (la retención se re-ancla) y su firma se recalcula:
    se comparan sus registros, no sus bytes.
    """

    def index(files):
        lines = files["metadata/artifacts.jsonl"].decode().splitlines()
        return {r["artifact_id"]: r["sha256"] for r in map(json.loads, lines)}

    assert index(before).items() <= index(after).items()
    for path, content in before.items():
        if not path.startswith("metadata/artifacts.jsonl"):
            assert after[path] == content, path


def test_a_crash_during_the_drain_can_be_closed_as_not_demonstrated(h):
    h.set_probes(database=lambda: (_ for _ in ()).throw(SystemExit(137)))
    h.start()
    with pytest.raises(SystemExit):
        h.execute()
    h.set_probes(database=h.backend.database)
    final = h.finalize(declare_executor_gone=True)
    assert final["decision"] == "NOT_DEMONSTRATED" and "drain:missing" in final["reasons"]


def test_a_crash_inside_finalize_resumes_with_identical_artifacts(h, monkeypatch):
    # (117, 120) Muere tras custodiar la reconciliación: se retoma y no se reescribe.
    original = executor._persist_once
    state = {"armed": True}

    def dies_at_calculation(store, run_state, artifact_type, payload, **kwargs):
        if artifact_type == executor.ART_CALCULATION and state.pop("armed", False):
            raise SystemExit(137)
        return original(store, run_state, artifact_type, payload, **kwargs)

    monkeypatch.setattr(executor, "_persist_once", dies_at_calculation)
    h.start()
    h.execute()
    with pytest.raises(SystemExit):
        h.finalize()
    reconciliation = h.state().record(executor.ART_RECONCILIATION)["sha256"]
    assert h.phase() is P.RECONCILING
    before = run_files(h.store, h.run_id)
    final = h.finalize()
    assert_preserved(before, run_files(h.store, h.run_id))
    assert final["decision"] == "PASS"
    assert h.state().record(executor.ART_RECONCILIATION)["sha256"] == reconciliation
    types = [record["artifact_type"] for record in h.state().records]
    assert len(types) == len(set(types))


def test_a_crash_after_the_decision_keeps_the_decision(h, monkeypatch):
    # (118, 113)
    h.start()
    h.execute()
    original = h.store.finalize_with_json_artifact
    state = {"armed": True}

    def dies_once(**kwargs):
        if state.pop("armed", False):
            raise SystemExit(137)
        return original(**kwargs)

    monkeypatch.setattr(h.store, "finalize_with_json_artifact", dies_once)
    with pytest.raises(SystemExit):
        h.finalize()
    assert h.phase() is P.DECIDED
    decision = h.state().record(executor.ART_DECISION)["sha256"]
    assert refusal(h.execute).terminal
    final = h.finalize()
    assert final["decision_sha256"] == decision and h.phase() is P.FINALIZED_PASS


def test_a_decided_run_never_takes_a_different_decision(h, monkeypatch):
    # (112) Si la base cambió tras la decisión, no se reescribe: la finalización se niega.
    h.start()
    h.execute()
    original = h.store.finalize_with_json_artifact
    monkeypatch.setattr(
        h.store, "finalize_with_json_artifact", lambda **kw: (_ for _ in ()).throw(SystemExit(1))
    )
    with pytest.raises(SystemExit):
        h.finalize()
    monkeypatch.setattr(h.store, "finalize_with_json_artifact", original)
    files = run_files(h.store, h.run_id)
    h.backend.rows.clear()
    with pytest.raises(executor.Ex10ExecutorError):
        h.finalize()
    assert run_files(h.store, h.run_id) == files and h.phase() is P.DECIDED


# --- CLI (§37) --------------------------------------------------------------------------------


@pytest.fixture
def cli(h, monkeypatch):
    monkeypatch.setattr(s11_ex10.Command, "_store", staticmethod(lambda: h.store))
    monkeypatch.setattr(s11_ex10.Command, "probes", lambda self: h.probes)

    def run(*args):
        out = io.StringIO()
        call_command("s11_ex10", *args, stdout=out)
        return json.loads(out.getvalue())

    return run


def start_args(nonce):
    return ("start", "--expected-git-sha", SHA, "--source-root", "/nonexistent", "--nonce", nonce,
            "--evidence-mode", LOCAL, "--confirm-new-run")  # fmt: skip


def test_the_cli_runs_each_step_separately(h, cli):
    started = cli(*start_args("nonce-cli-00001"))
    run_id = started["run_id"]
    assert cli("status", "--run-id", run_id)["phase"] == "PREPARED"
    executed = cli("execute", "--run-id", run_id, "--expected-git-sha", SHA, "--confirm-emission")
    assert executed["attempts"] == 50 and executed["drain"]["outcome"] == "COMPLETE"
    assert cli("reconcile", "--run-id", run_id, "--expected-git-sha", SHA)["decision"] == "PASS"
    final = cli("finalize", "--run-id", run_id, "--expected-git-sha", SHA)
    assert final["decision"] == "PASS"
    assert cli("status", "--run-id", run_id)["phase"] == "FINALIZED_PASS"


def test_the_cli_abandons_only_before_t0(h, cli):
    run_id = cli(*start_args("nonce-cli-00006"))["run_id"]
    final = cli("finalize", "--run-id", run_id, "--expected-git-sha", SHA,
                "--abandon-reason", "operator-stop")  # fmt: skip
    assert (final["decision"], final["lifecycle"]) == ("NOT_DEMONSTRATED", "ABANDONED_PRE_T0")
    with pytest.raises(CommandError) as info:
        cli("finalize", "--run-id", run_id, "--expected-git-sha", SHA)
    assert info.value.returncode == 2


@pytest.mark.parametrize(
    "args",
    [
        ("start", "--expected-git-sha", SHA, "--source-root", "/x", "--nonce", "nonce-cli-00002",
         "--evidence-mode", LOCAL),
        ("execute", "--run-id", RUN_ID, "--expected-git-sha", SHA),
        ("execute", "--run-id", "../etc", "--expected-git-sha", SHA, "--confirm-emission"),
        ("start", "--bogus"),
        ("explode",),
    ],
    ids=["no-confirm-new-run", "no-confirm-emission", "bad-run-id", "unknown-flag", "unknown-step"],
)  # fmt: skip
def test_cli_usage_errors_exit_3(cli, args):
    with pytest.raises(CommandError) as info:
        cli(*args)
    assert info.value.returncode == 3


def test_cli_refusals_have_their_own_exit_codes(h, cli, capsys):
    started = cli(*start_args("nonce-cli-00003"))
    capsys.readouterr()
    with pytest.raises(CommandError) as info:
        cli(*start_args("nonce-cli-00004"))
    assert info.value.returncode == 1
    stderr = json.loads(capsys.readouterr().err)
    assert stderr == {"refused": True, "terminal": False,
                      "reasons": [f"active-run:{started['run_id']}"]}  # fmt: skip
    cli("execute", "--run-id", started["run_id"], "--expected-git-sha", SHA, "--confirm-emission")
    with pytest.raises(CommandError) as info:
        cli(
            "execute",
            "--run-id",
            started["run_id"],
            "--expected-git-sha",
            SHA,
            "--confirm-emission",
        )
    assert info.value.returncode == 2


def test_cli_internal_errors_carry_no_values(h, cli):
    def boom(options):
        raise RuntimeError("postgres://user:secret@db/x")

    h.set_probes(preflight=boom)
    with pytest.raises(CommandError) as info:
        cli(*start_args("nonce-cli-00005"))
    assert info.value.returncode == 3
    assert str(info.value) == "S11_EX10_INTERNAL: RuntimeError"


# --- §57 Thread safety ------------------------------------------------------------------------


class InstantTransport:
    """Transporte en memoria: un 201 contractual inmediato con el reloj del generador."""

    def __init__(self):
        self.sent = []
        self.lock = threading.Lock()

    def send(self, request, *, clock, wall_clock):
        start, start_utc = clock(), wall_clock()
        with self.lock:
            self.sent.append(request.item.request_key)
        body = json.dumps(
            {"id": "00000000-0000-4000-8000-%012d" % request.item.index, "status": "CREADO",
             "reward": {"impact_points": 20, "credits": 20, "status": "PENDIENTE"}}
        ).encode()  # fmt: skip
        return ex10.Exchange(start, clock(), start_utc, wall_clock(), http_status=201, body=body)


def test_one_hundred_happy_paths_lose_nothing_duplicate_nothing_and_leak_nothing(dataset, plan):
    aliases = [user["alias"] for user in dataset.payload["users"]]
    prepared = ex10.prepare_requests(
        dataset,
        plan,
        run_id=RUN_ID,
        tokens=FakeTokens(aliases).mapping(),
        category_ids={i.category_slug: "00000000-0000-4000-8000-000000000001" for i in plan.items},
    )
    baseline = threading.active_count()
    for _ in range(100):
        transport = InstantTransport()
        run = ex10.LoadGenerator(plan, prepared, transport).run()
        assert sorted(transport.sent) == sorted(plan.by_key())  # sin pérdidas ni duplicados
        assert ex10.apparatus_invalidity(plan, run) == []
        assert [a.request_key for a in run.attempts] == [i.request_key for i in plan.items]
        assert all(a.send_count == 1 for a in run.attempts)
        assert vu_threads() == []
    assert threading.active_count() <= baseline


def test_ten_real_socket_runs_in_a_row(h):
    # Con sockets reales, 10 runs completos sin hilos filtrados ni conexiones de más.
    for index in range(10):
        h.backend.rows.clear()  # el reset de `prepare` que exige READY_STATE
        h.start()
        h.execute()
        assert h.finalize()["decision"] == "PASS"
        assert h.backend.accepted == 50 * (index + 1)
        assert vu_threads() == []


# --- Fase 3B: checkpoint LOCAL_TEST `rehearsal-started` ---------------------------------------

#: Lo que el checkpoint escribe: el START completo y el marcador de emisión. Nada más.
STARTED_ARTIFACTS = {
    executor.ART_MANIFEST_INITIAL,
    executor.ART_PREFLIGHT,
    executor.ART_PLAN,
    executor.ART_START,
    executor.ART_EMISSION,
}
REHEARSAL_RESULT_KEYS = {
    "run_id", "evidence_mode", "phase", "emission_armed", "t0_published", "tokens_issued",
    "requests_prepared", "requests_sent",
}  # fmt: skip


def rehearse(h, run_id=None, git_sha=SHA):
    return executor.rehearsal_started_run(
        h.store, run_id=run_id or h.run_id, git_sha=git_sha, probes=h.probes
    )


def _forbidden(name, calls):
    def probe(*args, **kwargs):
        calls.append(name)
        raise AssertionError(f"{name} no debe llamarse en rehearsal-started")

    return probe


@pytest.fixture
def no_emission(h, monkeypatch):
    """Todo lo que sigue al marcador en `execute` revienta si se llama y queda registrado."""
    calls: list[str] = []
    after_marker = ("fixture_result", "issue_tokens", "verify_tokens", "category_ids",
                    "resolve_backend", "transport", "generator")  # fmt: skip
    h.set_probes(**{name: _forbidden(name, calls) for name in after_marker})
    monkeypatch.setattr(
        executor.ex10_load, "prepare_requests", _forbidden("prepare_requests", calls)
    )
    monkeypatch.setattr(executor, "_arm_emission", _forbidden("_arm_emission", calls))
    monkeypatch.setattr(ex10.LoadGenerator, "run", _forbidden("LoadGenerator.run", calls))
    monkeypatch.setattr(socket, "create_connection", _forbidden("socket.create_connection", calls))
    return calls


def artifact_types(h, run_id=None):
    return [record["artifact_type"] for record in h.state(run_id).records]


def test_local_test_rehearsal_stops_at_started_without_any_emission_step(h, no_emission):
    # (1, 13–24) PREPARED → STARTED: un único marcador; ni tokens, ni cuerpos, ni transporte,
    # ni generador, ni armado, ni red, ni filas, ni eventos.
    h.start()
    result = rehearse(h)
    assert result == {
        "run_id": h.run_id, "evidence_mode": LOCAL, "phase": "STARTED", "emission_armed": False,
        "t0_published": False, "tokens_issued": 0, "requests_prepared": 0, "requests_sent": 0,
    }  # fmt: skip
    assert h.phase() is P.STARTED
    types = artifact_types(h)
    assert types.count(executor.ART_EMISSION) == 1 and set(types) == STARTED_ARTIFACTS
    assert "t0_ns" not in h.payload(executor.ART_EMISSION)
    assert no_emission == []
    assert h.tokens == [] and h.backend.connections == 0 and h.backend.accepted == 0
    assert h.backend.rows == {}


def test_rehearsal_refuses_an_official_run_without_writing(h, no_emission):
    # (2) El modo sale del manifest custodiado: OFFICIAL se rechaza en el dominio, sin escribir.
    h.start(evidence_mode=OFFICIAL)
    files = run_files(h.store, h.run_id)
    rejected = refusal(rehearse, h)
    assert rejected.terminal
    assert rejected.reasons == ("evidence-mode:rehearsal-started-requires-LOCAL_TEST",)
    assert run_files(h.store, h.run_id) == files and h.phase() is P.PREPARED
    assert no_emission == []


def test_rehearsal_rechecks_the_mode_under_the_lock(h, monkeypatch, no_emission):
    # Entre la comprobación previa y el lock el manifest no puede pasar a OFFICIAL sin rechazo.
    h.start()
    original, calls = executor._open_state, {"n": 0}

    def official_inside_lock(store, run_id, git_sha):
        state = original(store, run_id, git_sha)
        calls["n"] += 1
        if calls["n"] >= 3:  # 1: rehearsal, 2: helper fuera del lock, 3: dentro del lock
            state.payloads[executor.ART_MANIFEST_INITIAL] = {
                **state.initial_manifest,
                "experiment_id": executor.EXPERIMENT_IDS[OFFICIAL],
            }
        return state

    monkeypatch.setattr(executor, "_open_state", official_inside_lock)
    files = run_files(h.store, h.run_id)
    assert refusal(rehearse, h).terminal
    assert run_files(h.store, h.run_id) == files and h.phase() is P.PREPARED


def _created(h, monkeypatch):
    original = executor._persist

    def dies_at_preflight(store, run_id, artifact_type, payload, **kwargs):
        if artifact_type == executor.ART_PREFLIGHT:
            raise SystemExit(137)
        return original(store, run_id, artifact_type, payload, **kwargs)

    monkeypatch.setattr(executor, "_persist", dies_at_preflight)
    with pytest.raises(SystemExit):
        h.start(nonce="nonce-rehearsal-created")
    monkeypatch.setattr(executor, "_persist", original)
    (run_id,) = (path.name for path in (h.store.root / "runs").iterdir())
    h.run_ids.append(run_id)


class _DiesOnRun:
    def run(self):
        raise SystemExit(137)


def _to_phase(h, monkeypatch, phase):
    if phase == "CREATED":
        return _created(h, monkeypatch)
    h.start()
    if phase == "STARTED":
        rehearse(h)
    elif phase == "EMITTING":
        h.set_probes(generator=lambda plan, prepared, transport: _DiesOnRun())
        with pytest.raises(SystemExit):
            h.execute()
    elif phase == "STOPPED_PRE_T0":
        h.set_probes(fixture_result=lambda specs: (_ for _ in ()).throw(RuntimeError("x")))
        h.execute()
    elif phase == "RECONCILING":
        h.execute()
    elif phase == "ABANDONED_PRE_T0":
        h.finalize(abandon_reason="operator-stop")


@pytest.mark.parametrize(
    "phase", ["CREATED", "STARTED", "EMITTING", "STOPPED_PRE_T0", "RECONCILING", "ABANDONED_PRE_T0"]
)
def test_rehearsal_only_accepts_prepared_and_refuses_the_rest_without_writing(
    h, monkeypatch, phase
):
    # (4–7, 10) Sólo PREPARED; un segundo checkpoint sobre STARTED no duplica el marcador.
    _to_phase(h, monkeypatch, phase)
    assert str(h.phase()) == phase
    files = run_files(h.store, h.run_id)
    rejected = refusal(rehearse, h)
    assert rejected.terminal
    assert run_files(h.store, h.run_id) == files and str(h.phase()) == phase


def test_rehearsal_refuses_another_head(h, no_emission):
    # (8) El SHA esperado debe ser el del START.
    h.start()
    files = run_files(h.store, h.run_id)
    rejected = refusal(rehearse, h, git_sha="1" * 40)
    assert rejected.terminal and rejected.reasons == ("git-sha-differs-from-start",)
    assert run_files(h.store, h.run_id) == files


def test_rehearsal_applies_the_execute_freshness_bound(h, monkeypatch, no_emission):
    # (9) La misma frontera de frescura que `execute`, y la del preflight completo.
    now = {"value": FRESH}
    h.set_probes(
        now=lambda: now["value"],
        preflight=lambda options: preflight_report(git_sha=SHA, checked_at=FRESH),
    )
    h.start()
    files = run_files(h.store, h.run_id)
    now["value"] = FRESH + timedelta(minutes=15) + US
    rejected = refusal(rehearse, h)
    assert "preflight:stale" in rejected.reasons
    assert not rejected.terminal and run_files(h.store, h.run_id) == files
    # Sin la revalidación del informe, el checkpoint compartido aplica la frontera de execute.
    with monkeypatch.context() as patch:
        patch.setattr(executor, "preflight_problems", lambda report, **kwargs: [])
        assert refusal(rehearse, h).reasons == ("preflight-stale",)
    assert run_files(h.store, h.run_id) == files
    now["value"] = FRESH + timedelta(minutes=15)
    assert rehearse(h)["phase"] == "STARTED"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"git_sha": "1" * 40}, "preflight:git_sha"),
        ({"result": "FAIL"}, "preflight:result"),
        ({"result": "INCOMPLETE"}, "preflight:result"),
        ({"gate": "NOT_READY"}, "preflight:gate"),
        ({"ready_for_execution": True}, "preflight:ready_for_execution"),
        ({"methodology_version": "S11A-v9"}, "preflight:methodology_version"),
    ],
    ids=["wrong-sha", "fail", "incomplete", "wrong-gate", "ready-true", "methodology"],
)
def test_rehearsal_revalidates_the_custodied_preflight(h, monkeypatch, no_emission, change, reason):
    # (10–12) El preflight custodiado en START se revalida entero, no sólo su edad.
    h.start()
    original = executor.load_state

    def altered(store, run_id):
        state = original(store, run_id)
        state.payloads[executor.ART_PREFLIGHT] = {
            **state.payloads[executor.ART_PREFLIGHT],
            **change,
        }
        return state

    monkeypatch.setattr(executor, "load_state", altered)
    files = run_files(h.store, h.run_id)
    rejected = refusal(rehearse, h)
    assert reason in rejected.reasons and not rejected.terminal
    assert run_files(h.store, h.run_id) == files


def test_rehearsal_requires_verified_custody(h, no_emission):
    h.start()
    plan_file = next(
        path for path in (h.store.root / "runs" / h.run_id).rglob(f"{executor.ART_PLAN}.json")
    )
    plan_file.chmod(0o600)
    plan_file.write_bytes(plan_file.read_bytes().replace(b"{", b"{ ", 1))
    rejected = refusal(rehearse, h)
    assert any(reason.startswith("custody:") for reason in rejected.reasons)
    assert executor.ART_EMISSION not in artifact_types(h)


def test_execute_and_rehearsal_create_the_marker_through_the_same_function(h, monkeypatch):
    # (16) Una sola ruta para el marcador: misma función, mismo esquema y mismos campos.
    original, steps = executor._checkpoint_started, []

    def spy(*args, **kwargs):
        steps.append(kwargs["step"])
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "_checkpoint_started", spy)
    h.start()
    h.execute()
    official_marker = h.payload(executor.ART_EMISSION)
    h.finalize()
    h.start()
    rehearse(h)
    rehearsal_marker = h.payload(executor.ART_EMISSION)
    assert steps == ["execute", "rehearsal-started"]
    variable = {"run_id", "recorded_at"}
    assert set(official_marker) == set(rehearsal_marker)
    assert {k: v for k, v in official_marker.items() if k not in variable} == {
        k: v for k, v in rehearsal_marker.items() if k not in variable
    }
    assert canonical_json_bytes({**rehearsal_marker, "run_id": "x", "recorded_at": "y"}) == (
        canonical_json_bytes({**official_marker, "run_id": "x", "recorded_at": "y"})
    )


def test_an_interruption_during_the_checkpoint_keeps_each_path_semantics(h, monkeypatch):
    # La interrupción se difiere hasta custodiar el marcador: `execute` se detiene antes de t0
    # (como antes del refactor) y el checkpoint LOCAL_TEST queda en STARTED, su objetivo.
    original = executor._persist

    def interrupted(store, run_id, artifact_type, payload, **kwargs):
        record = original(store, run_id, artifact_type, payload, **kwargs)
        if artifact_type == executor.ART_EMISSION:
            os.kill(os.getpid(), signal.SIGINT)
        return record

    monkeypatch.setattr(executor, "_persist", interrupted)
    h.start()
    stopped = h.execute()
    assert stopped["phase"] == "STOPPED_PRE_T0" and h.backend.accepted == 0
    h.finalize(abandon_reason="operator-stop")
    h.start()
    with pytest.raises(KeyboardInterrupt):
        rehearse(h)
    assert h.phase() is P.STARTED and artifact_types(h).count(executor.ART_EMISSION) == 1


def test_started_rehearsal_is_visible_in_status_and_abandons_before_t0(h, no_emission):
    # (25–29) status informa STARTED sin escribir; el abandono es el canónico; el LOCAL_TEST
    # cerrado no entra en el primer run oficial ni impide un START OFFICIAL posterior.
    h.start()
    rehearse(h)
    files = run_files(h.store, h.run_id)
    assert executor.run_status(h.store, h.run_id)["phase"] == "STARTED"
    assert run_files(h.store, h.run_id) == files
    final = h.finalize(abandon_reason="rehearsal-complete")
    assert final["lifecycle"] == "ABANDONED_PRE_T0"
    assert final["decision"] == "NOT_DEMONSTRATED" and final["binding"] is False
    assert executor.run_status(h.store, h.run_id)["phase"] == "ABANDONED_PRE_T0"
    assert no_emission == []
    prior = executor.prior_ex10_runs(h.store)
    assert [(run.evidence_mode, run.finalized, run.problems) for run in prior] == [
        (LOCAL, True, ())
    ]
    assert executor.binding_refusal(prior, evidence_mode=OFFICIAL, acknowledged=()) == ([], False)
    assert h.start(evidence_mode=OFFICIAL)["phase"] == "PREPARED"


def test_the_cli_rehearsal_started_step(h, cli, capsys):
    # (3, 30) Confirmación propia obligatoria, salida JSON saneada y sin opciones de emisión.
    run_id = cli(*start_args("nonce-cli-rehearsal-1"))["run_id"]
    base = ("rehearsal-started", "--run-id", run_id, "--expected-git-sha", SHA)
    confirm = "--confirm-local-rehearsal"
    for extra in ((), ("--confirm-emission", confirm), ("--evidence-mode", LOCAL, confirm),
                  ("--official", confirm), ("--request-count", "50", confirm)):  # fmt: skip
        with pytest.raises(CommandError) as info:
            cli(*base, *extra)
        assert info.value.returncode == 3
    assert executor.ART_EMISSION not in artifact_types(h, run_id)
    result = cli(*base, "--confirm-local-rehearsal")
    assert set(result) == REHEARSAL_RESULT_KEYS and result["phase"] == "STARTED"
    assert cli("status", "--run-id", run_id)["phase"] == "STARTED"
    capsys.readouterr()
    with pytest.raises(CommandError) as info:
        cli(*base, "--confirm-local-rehearsal")
    assert info.value.returncode == 2
    assert json.loads(capsys.readouterr().err) == {
        "refused": True, "terminal": True,
        "reasons": ["phase:STARTED:rehearsal-started-not-allowed"],
    }  # fmt: skip
    final = cli("finalize", "--run-id", run_id, "--expected-git-sha", SHA,
                "--abandon-reason", "rehearsal-complete")  # fmt: skip
    assert final["lifecycle"] == "ABANDONED_PRE_T0"


def test_the_cli_refuses_rehearsal_on_an_official_run(h, cli, capsys):
    official = [*start_args("nonce-cli-rehearsal-2")]
    official[official.index("--evidence-mode") + 1] = OFFICIAL
    started = cli(*official)
    capsys.readouterr()
    with pytest.raises(CommandError) as info:
        cli("rehearsal-started", "--run-id", started["run_id"], "--expected-git-sha", SHA,
            "--confirm-local-rehearsal")  # fmt: skip
    assert info.value.returncode == 2
    assert json.loads(capsys.readouterr().err)["reasons"] == [
        "evidence-mode:rehearsal-started-requires-LOCAL_TEST"
    ]


# --- status estrictamente read-only -----------------------------------------------------------
# `status` no crea, repara ni normaliza nada: cada byte y cada campo de metadata (tipo, modo,
# dueño, tamaño, mtime, ctime, inodo, destino de symlink, SHA-256) del árbol de la custodia,
# directorios incluidos, queda idéntico. El `WriteSpy` hace fallar cualquier primitiva de
# escritura mientras corre. Sólo después, como oráculo del schema, se calcula el status anterior
# a la corrección (que inicializaba el run).


def tree(base: Path) -> dict | None:
    """Metadata completa (salvo atime) de `base` y de todo lo que contiene, sin seguir symlinks."""
    if not os.path.lexists(base):
        return None
    facts, pending = {}, [Path(base)]
    while pending:
        path = pending.pop()
        info = os.lstat(path)
        fact = {
            "type": stat.S_IFMT(info.st_mode), "mode": stat.S_IMODE(info.st_mode),
            "uid": info.st_uid, "gid": info.st_gid, "size": info.st_size, "nlink": info.st_nlink,
            "mtime": info.st_mtime_ns, "ctime": info.st_ctime_ns, "ino": info.st_ino,
        }  # fmt: skip
        if stat.S_ISLNK(info.st_mode):
            fact["target"] = os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            fact["sha256"] = sha256_bytes(path.read_bytes())
        elif stat.S_ISDIR(info.st_mode):
            fact["entries"] = sorted(os.listdir(path))
            pending.extend(path / name for name in fact["entries"])
        facts[str(path.relative_to(base))] = fact
    return facts


_OS_WRITES = ("mkdir", "makedirs", "chmod", "fchmod", "lchmod", "chown", "fchown", "lchown",
              "rename", "renames", "replace", "unlink", "remove", "removedirs", "rmdir",
              "truncate", "ftruncate", "fsync", "fdatasync", "utime", "link", "symlink", "write",
              "mkfifo", "mknod")  # fmt: skip
_PATH_WRITES = ("mkdir", "chmod", "lchmod", "touch", "write_text", "write_bytes", "unlink",
                "rename", "replace", "rmdir", "symlink_to", "hardlink_to")  # fmt: skip
_STORE_MUTATIONS = ("initialize_run", "records", "verify", "persist_json", "persist_bytes",
                    "append_jsonl", "register_existing", "finalize_verification_retention",
                    "finalize_with_json_artifact", "recover_finalization", "write_checksums",
                    "write_index_signature", "_append_index", "mutation", "_mutation_lock",
                    "_atomic_write", "_atomic_replace", "_write_finalized_seal")  # fmt: skip
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


@contextmanager
def write_spy():
    """Toda primitiva que escribe, crea, cambia permisos o toma el lock exclusivo falla y queda
    registrada; las lecturas (`open` sin escritura, `os.open` sin flags de escritura, el
    `flock` compartido) pasan."""
    calls: list[str] = []

    def guard(name, original=None, allowed=None):
        def call(*args, **kwargs):
            if allowed is not None and allowed(*args, **kwargs):
                return original(*args, **kwargs)
            calls.append(name)
            raise AssertionError(f"status no puede llamar {name}")

        return call

    def read_mode(file, mode="r", *args, **kwargs):
        return not set(mode) & set("wax+")

    with pytest.MonkeyPatch.context() as mp:
        for name in _OS_WRITES:
            if hasattr(os, name):
                mp.setattr(os, name, guard(f"os.{name}"))
        reads = lambda path, flags, *args, **kwargs: not flags & _WRITE_FLAGS  # noqa: E731
        mp.setattr(os, "open", guard("os.open:write", os.open, reads))
        reading = guard("open:write", io.open, read_mode)
        mp.setattr(io, "open", reading)
        mp.setattr(builtins, "open", reading)
        shared = lambda fd, operation: not operation & fcntl.LOCK_EX  # noqa: E731
        mp.setattr(fcntl, "flock", guard("fcntl.flock:LOCK_EX", fcntl.flock, shared))
        mp.setattr(fcntl, "lockf", guard("fcntl.lockf"))
        for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile"):
            mp.setattr(tempfile, name, guard(f"tempfile.{name}"))
        for name in ("copy", "copy2", "copyfile", "copytree", "move", "rmtree"):
            mp.setattr(shutil, name, guard(f"shutil.{name}"))
        for name in _PATH_WRITES:
            if hasattr(Path, name):
                mp.setattr(Path, name, guard(f"Path.{name}"))
        for name in _STORE_MUTATIONS:
            mp.setattr(EvidenceStore, name, guard(f"EvidenceStore.{name}"))
        for name in ("load_state", "_persist", "_persist_once"):
            mp.setattr(executor, name, guard(f"executor.{name}"))
        yield calls


def legacy_status(store, run_id):
    """El `run_status` anterior a la corrección (inicializa el run): oráculo del schema."""
    if not executor.run_exists(store, run_id):
        return {"run_id": run_id, "exists": False}
    state = executor.load_state(store, run_id)
    decision = state.payload(executor.ART_DECISION)
    return {
        "run_id": run_id,
        "exists": True,
        "scenario_id": (state.initial_manifest or {}).get("scenario_id"),
        "phase": str(executor.phase_of(state)) if state.initial_manifest else None,
        "finalized": state.finalized,
        "artifacts": sorted(record["artifact_type"] for record in state.records),
        "decision": decision.get("decision")
        if isinstance(decision, Mapping) and state.finalized
        else None,
        "custody_errors": store.verify(run_id),
    }


def readonly_status(store, run_id, *, times=1):
    """`times` status bajo el spy; el árbol completo de la custodia no puede cambiar."""
    base = store.root.parent
    before = tree(base)
    with write_spy() as calls:
        results = [executor.run_status(store, run_id) for _ in range(times)]
    assert calls == []
    assert tree(base) == before
    assert all(result == results[0] for result in results)
    return results[0]


def _reach(h, monkeypatch, phase):
    """Lleva el run de `h` a `phase` por el camino real de cada fase."""
    if phase in {"CREATED", "STARTED", "EMITTING", "STOPPED_PRE_T0", "RECONCILING",
                 "ABANDONED_PRE_T0"}:  # fmt: skip
        _to_phase(h, monkeypatch, phase)
    elif phase == "PREPARED":
        h.start()
    elif phase == "DRAINING":
        database, calls = h.probes.database, []

        def dies_once():
            calls.append(1)
            if len(calls) == 1:
                raise SystemExit(137)
            return database()

        h.set_probes(database=dies_once)
        h.start()
        with pytest.raises(SystemExit):
            h.execute()
    elif phase == "DECIDED":
        h.start()
        h.execute()
        original, armed = h.store.finalize_with_json_artifact, {"armed": True}

        def dies_once(**kwargs):
            if armed.pop("armed", False):
                raise SystemExit(137)
            return original(**kwargs)

        monkeypatch.setattr(h.store, "finalize_with_json_artifact", dies_once)
        with pytest.raises(SystemExit):
            h.finalize()
    elif phase == "NOT_DEMONSTRATED":
        h.set_probes(database=lambda: (_ for _ in ()).throw(SystemExit(137)))
        h.start()
        with pytest.raises(SystemExit):
            h.execute()
        h.set_probes(database=h.backend.database)
        h.finalize(declare_executor_gone=True)
    elif phase == "FINALIZED_PASS":
        h.start()
        h.execute()
        h.finalize()
    elif phase == "FINALIZED_FAIL":
        h.backend.modes[h.plan.for_vu(4)[3].request_key] = "500"
        h.start()
        h.execute()
        h.finalize()
    monkeypatch.undo()


STATUS_PHASES = ["CREATED", "PREPARED", "STARTED", "EMITTING", "STOPPED_PRE_T0", "DRAINING",
                 "RECONCILING", "DECIDED", "ABANDONED_PRE_T0", "NOT_DEMONSTRATED",
                 "FINALIZED_PASS", "FINALIZED_FAIL"]  # fmt: skip


@pytest.mark.parametrize("phase", STATUS_PHASES)
def test_status_is_strictly_read_only_in_every_phase(h, monkeypatch, phase):
    # (1–12) Fase correcta, mismo schema y mismo resultado que el status anterior, sin tocar
    # un solo byte ni campo de metadata.
    _reach(h, monkeypatch, phase)
    status = readonly_status(h.store, h.run_id)
    assert status["phase"] == phase and status["exists"] is True
    assert status["custody_errors"] == []
    assert status == legacy_status(h.store, h.run_id)


def test_the_spy_catches_the_previous_mutating_status(h):
    # El spy no es vacuo: el status anterior (`initialize_run`: mkdir/chmod) no pasa.
    h.start()
    with write_spy() as calls, pytest.raises(AssertionError):
        legacy_status(h.store, h.run_id)
    assert calls and calls[0] == "executor.load_state"
    with write_spy() as calls, pytest.raises(AssertionError):
        h.store.initialize_run(h.run_id)
    with write_spy() as calls, pytest.raises(AssertionError):
        os.chmod(h.store.root, 0o700)
    assert calls == ["os.chmod"]


MISSING = "s11-12345678-1234-5234-8234-123456789012"


def test_status_of_a_missing_run_creates_nothing(h):
    # (13) Ni el run, ni subdirectorios, ni índice, ni lock; `runs/` no cambia su ctime.
    h.start()
    runs = h.store.root / "runs"
    ctime = os.lstat(runs).st_ctime_ns
    assert readonly_status(h.store, MISSING) == {"run_id": MISSING, "exists": False}
    assert not os.path.lexists(runs / MISSING) and os.lstat(runs).st_ctime_ns == ctime


@pytest.mark.parametrize(
    "run_id",
    ["", "../runs", "s11-x", RUN_ID.upper(), RUN_ID + "/..", RUN_ID + "/metadata", "not-a-run-id"],
)
def test_status_of_an_invalid_run_id_creates_nothing(h, cli, run_id):
    # (14) Un `run_id` no canónico no existe, aunque un directorio se llame así.
    h.start()
    (h.store.root / "runs" / "not-a-run-id").mkdir()
    assert readonly_status(h.store, run_id) == {"run_id": run_id, "exists": False}
    before = tree(h.store.root.parent)
    with pytest.raises(CommandError) as info:
        cli("status", "--run-id", run_id)
    assert info.value.returncode == 3 and str(info.value) == "S11_EX10_USAGE: run-id"
    assert tree(h.store.root.parent) == before


def _index(h):
    return h.store.root / "runs" / h.run_id / "metadata" / "artifacts.jsonl"


def test_status_without_an_index_reports_it_without_recreating_it(h):
    # (15) Run incompleto: nada indexado y la firma no corresponde; no se recrea el índice.
    h.start()
    _index(h).unlink()
    status = readonly_status(h.store, h.run_id)
    assert (status["phase"], status["artifacts"]) == (None, [])
    assert status["custody_errors"] == ["index-signature"]
    assert not _index(h).exists() and status == legacy_status(h.store, h.run_id)


def test_status_with_a_corrupt_index_is_a_sanitized_error(h, cli):
    # (16) Como antes: un error saneado, nunca una reparación.
    h.start()
    with _index(h).open("ab") as index:
        index.write(b"{not json\n")
    base = h.store.root.parent
    before = tree(base)
    with write_spy() as calls, pytest.raises(ValueError):
        executor.run_status(h.store, h.run_id)
    with pytest.raises(CommandError) as info:
        cli("status", "--run-id", h.run_id)
    assert info.value.returncode == 3 and str(info.value) == "S11_EX10_ERROR: JSONDecodeError"
    assert calls == [] and tree(base) == before


def _record(h, artifact_type):
    record = h.state().record(artifact_type)
    return record, h.store.root / "runs" / h.run_id / record["relative_path"]


@pytest.mark.parametrize("damage", ["missing", "sha256", "hmac"])
def test_status_reports_damaged_custody_without_repairing_it(h, damage):
    # (17–19) Artefacto ausente, hash o firma del índice alterados: se informa, no se repara.
    h.start()
    rehearse(h)
    record, path = _record(h, executor.ART_PLAN)
    if damage == "missing":
        path.unlink()
    elif damage == "sha256":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        signature = _index(h).with_suffix(".jsonl.hmac")
        signature.write_text("0" * 64 + "\n", encoding="ascii")
    status = readonly_status(h.store, h.run_id)
    expected = {"missing": f"missing:{record['artifact_id']}",
                "sha256": f"sha256:{record['artifact_id']}", "hmac": "index-signature"}  # fmt: skip
    assert status["custody_errors"] == [expected[damage]] and status["phase"] == "STARTED"
    assert (damage == "missing") is not path.exists()
    assert status == legacy_status(h.store, h.run_id)


def test_status_never_follows_a_symlinked_run_or_runs_directory(h):
    # (20) Un symlink no es un run: no se lee a través de él ni se toca su destino.
    h.start()
    runs = h.store.root / "runs"
    elsewhere = h.store.root.parent / "elsewhere"
    elsewhere.mkdir()
    (runs / h.run_id).rename(elsewhere / h.run_id)
    (runs / h.run_id).symlink_to(elsewhere / h.run_id, target_is_directory=True)
    assert readonly_status(h.store, h.run_id) == {"run_id": h.run_id, "exists": False}
    (runs / h.run_id).unlink()
    (elsewhere / h.run_id).rename(runs / h.run_id)
    runs.rename(h.store.root / "runs-real")
    runs.symlink_to(h.store.root / "runs-real", target_is_directory=True)
    assert readonly_status(h.store, h.run_id) == {"run_id": h.run_id, "exists": False}


def test_status_of_a_run_path_that_is_not_a_directory(h):
    # (21)
    h.start()
    (h.store.root / "runs" / MISSING).write_bytes(b"x")
    assert readonly_status(h.store, MISSING) == {"run_id": MISSING, "exists": False}


@pytest.mark.parametrize("created", [False, True], ids=["no-evidence-root", "no-runs-dir"])
def test_status_never_creates_the_evidence_root_or_runs(created):
    # (22, 23) Sin raíz de evidencia o sin `runs/`: nada se crea, ni el lock.
    store = make_store(f"status-roots-{created}")
    try:
        if created:
            store.root.mkdir(mode=0o700)
        assert readonly_status(store, MISSING) == {"run_id": MISSING, "exists": False}
        assert store.root.exists() is created and not (store.root / "runs").exists()
        assert not (store.root / ".s11-custody.lock").exists()
    finally:
        remove_store(store)


@pytest.mark.parametrize("phase", ["STARTED", "ABANDONED_PRE_T0", "missing"])
def test_one_hundred_statuses_leave_everything_identical(h, monkeypatch, phase):
    # (24–26)
    if phase == "missing":
        h.start()
        assert readonly_status(h.store, MISSING, times=100)["exists"] is False
        return
    _reach(h, monkeypatch, phase)
    assert readonly_status(h.store, h.run_id, times=100)["phase"] == phase


def _concurrently(count, call):
    results, errors = [], []

    def worker():
        try:
            results.append(call())
        except BaseException as exc:  # noqa: BLE001 - el test lo informa
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


def test_concurrent_statuses_agree_and_leave_everything_identical(h):
    # (27)
    h.start()
    rehearse(h)
    expected = readonly_status(h.store, h.run_id)
    base = h.store.root.parent
    before = tree(base)
    with write_spy() as calls:
        results, errors = _concurrently(
            16, lambda: [executor.run_status(h.store, h.run_id) for _ in range(10)]
        )
    assert errors == [] and calls == [] and tree(base) == before
    assert [status for batch in results for status in batch] == [expected] * 160


def test_status_during_a_mutation_is_coherent_or_a_read_race(h):
    # (28) Mientras otro paso muta, cada status es uno de los estados custodiados o
    # `status-read-race`; nunca una mezcla. Los lectores dejan un respiro entre lecturas: el
    # `flock` de Linux no da prioridad al mutador que espera el lock exclusivo.
    h.start()
    observed, errors, done = [], [], threading.Event()

    def poll():
        while not done.is_set():
            try:
                observed.append(executor.run_status(h.store, h.run_id))
            except executor.Ex10Refusal as exc:
                observed.append(exc.reasons)
            except BaseException as exc:  # noqa: BLE001 - el test lo informa
                errors.append(exc)
            time.sleep(0.002)

    states = [executor.run_status(h.store, h.run_id)]
    pollers = [threading.Thread(target=poll) for _ in range(4)]
    for poller in pollers:
        poller.start()
    try:
        rehearse(h)
        states.append(executor.run_status(h.store, h.run_id))
        h.finalize(abandon_reason="rehearsal-complete")
        states.append(executor.run_status(h.store, h.run_id))
    finally:
        done.set()
        for poller in pollers:
            poller.join()
    assert errors == [] and observed
    assert [state["phase"] for state in states] == ["PREPARED", "STARTED", "ABANDONED_PRE_T0"]
    assert all(state["custody_errors"] == [] for state in states)
    assert all(status in states or status == ("status-read-race",) for status in observed)


def test_a_write_while_reading_the_index_is_a_read_race(h, monkeypatch):
    # El índice (o su firma, o el sello) cambia durante la lectura: no se compone un estado.
    h.start()
    original = executor._read_payloads

    def touched(run_root, records):
        os.utime(_index(h))
        return original(run_root, records)

    monkeypatch.setattr(executor, "_read_payloads", touched)
    rejected = refusal(executor.run_status, h.store, h.run_id)
    assert rejected.reasons == ("status-read-race",) and not rejected.terminal


def test_the_readonly_loader_refuses_a_concurrent_write_by_itself(h, monkeypatch):
    # El loader es una ruta pública propia: su comprobación no depende de la de `run_status`.
    h.start()
    original = executor._read_payloads

    def touched(run_root, records):
        os.utime(_index(h))
        return original(run_root, records)

    monkeypatch.setattr(executor, "_read_payloads", touched)
    rejected = refusal(executor.load_existing_state_readonly, h.store, h.run_id)
    assert rejected.reasons == ("status-read-race",) and not rejected.terminal
    monkeypatch.undo()
    state = executor.load_existing_state_readonly(h.store, h.run_id)
    assert executor.phase_of(state) is P.PREPARED
    assert executor.load_existing_state_readonly(h.store, MISSING) is None


def test_a_symlinked_custody_lock_is_never_followed(h, cli):
    # Un lock sustituido por un symlink no se sigue (`O_NOFOLLOW`): error saneado, nada cambia.
    h.start()
    lock = h.store.root / ".s11-custody.lock"
    target = h.store.root.parent / "lock-target"
    lock.rename(target)
    lock.symlink_to(target)
    before = tree(h.store.root.parent)
    with write_spy() as calls, pytest.raises(OSError):
        executor.run_status(h.store, h.run_id)
    with pytest.raises(CommandError) as info:
        cli("status", "--run-id", h.run_id)
    assert info.value.returncode == 3 and str(info.value) == "S11_EX10_INTERNAL: OSError"
    assert calls == [] and tree(h.store.root.parent) == before


def test_a_write_while_verifying_is_a_read_race(h, monkeypatch):
    h.start()
    original = executor.verify_readonly
    signature = _index(h).with_suffix(".jsonl.hmac")

    def replaced(store, state):
        errors = original(store, state)
        copy = signature.with_name("replaced.tmp")
        copy.write_bytes(signature.read_bytes())
        os.replace(copy, signature)
        return errors

    monkeypatch.setattr(executor, "verify_readonly", replaced)
    rejected = refusal(executor.run_status, h.store, h.run_id)
    assert rejected.reasons == ("status-read-race",) and not rejected.terminal


def _holding_the_custody_lock(store, release):
    held = threading.Event()

    def hold():
        with store.mutation():
            held.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(10)
    return thread


def test_status_waits_for_a_mutator_and_never_takes_the_lock_exclusively(h, monkeypatch):
    # Un mutador que retiene el lock más que el plazo deja `status-read-race`, sin tocar nada;
    # uno breve sólo retrasa la lectura.
    h.start()
    expected = readonly_status(h.store, h.run_id)
    monkeypatch.setattr(executor, "STATUS_LOCK_ATTEMPTS", 3)
    monkeypatch.setattr(executor, "STATUS_LOCK_PAUSE", 0.01)
    release = threading.Event()
    thread = _holding_the_custody_lock(h.store, release)
    try:
        before = tree(h.store.root.parent)
        with write_spy() as calls:
            rejected = refusal(executor.run_status, h.store, h.run_id)
        assert rejected.reasons == ("status-read-race",) and not rejected.terminal
        assert calls == [] and tree(h.store.root.parent) == before
    finally:
        release.set()
        thread.join()
    monkeypatch.setattr(executor, "STATUS_LOCK_ATTEMPTS", 40)
    monkeypatch.setattr(executor, "STATUS_LOCK_PAUSE", 0.05)
    release = threading.Event()
    thread = _holding_the_custody_lock(h.store, release)
    threading.Timer(0.2, release.set).start()
    try:
        assert executor.run_status(h.store, h.run_id) == expected
    finally:
        release.set()
        thread.join()


def test_the_cli_status_is_strictly_read_only(h, cli):
    # (§20) La CLI imprime el mismo payload sin escribir.
    h.start()
    rehearse(h)
    expected = readonly_status(h.store, h.run_id)
    base = h.store.root.parent
    before = tree(base)
    with write_spy() as calls:
        printed = cli("status", "--run-id", h.run_id)
    assert printed == expected and calls == [] and tree(base) == before


# --- Exploración de runs previos estrictamente read-only --------------------------------------
# START explora los runs previos bajo su propio lock exclusivo, sin `initialize_run`, `mkdir`
# ni `chmod`, y sin pedir un segundo lock. Fuera de START, `prior_ex10_runs` lee bajo un único
# lock compartido. Cada run previo queda idéntico byte a byte y en toda su metadata.


def scanned(store, *, times=1):
    """`prior_ex10_runs` (`times` veces) bajo el spy; el árbol completo de la custodia no cambia."""
    base = store.root.parent
    before = tree(base)
    with write_spy() as calls:
        results = [executor.prior_ex10_runs(store) for _ in range(times)]
    assert calls == [] and tree(base) == before
    assert all(result == results[0] for result in results)
    return results[0]


def _no_nested_lock(store):
    raise AssertionError("START no puede pedir otro lock de custodia")


def start_keeping(h, keep, **kwargs):
    """START (o su rechazo) con su exploración bajo el spy y sin lock anidado: cada ruta de
    `keep` (runs previos) queda idéntica."""
    before = {path: tree(path) for path in keep}
    original, calls = executor._prior_ex10_runs_under_caller_lock, []

    def spied(store):
        with write_spy() as blocked:
            try:
                return original(store)
            finally:
                calls.extend(blocked)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(executor, "_prior_ex10_runs_under_caller_lock", spied)
        mp.setattr(executor, "_shared_custody_lock", _no_nested_lock)
        try:
            result = h.start(**kwargs)
        except executor.Ex10Refusal as exc:
            result = exc
    assert calls == []
    assert {path: tree(path) for path in keep} == before
    return result


def run_path(h, run_id):
    return h.store.root / "runs" / run_id


def _closed(h, mode, how, **kwargs):
    """Un run cerrado de `mode`: abandonado antes de t0, o PASS/FAIL por emisión real."""
    run_id = h.start(evidence_mode=mode, **kwargs)["run_id"]
    if how == "abandoned":
        h.finalize(abandon_reason="operator-stop")
        return run_id
    if how == "fail":
        h.backend.modes[h.plan.for_vu(4)[3].request_key] = "500"
    h.execute()
    h.finalize()
    h.backend.modes.clear()
    return run_id


@pytest.mark.parametrize("layout", ["no-evidence-root", "no-runs-dir", "empty-runs-dir"])
def test_prior_scan_of_an_empty_root_creates_nothing(layout):
    # (1–4) Sin raíz, sin `runs/` o con `runs/` vacío: vacío, sin crear nada ni el lock.
    store = make_store(f"prior-{layout}")
    try:
        if layout != "no-evidence-root":
            store.root.mkdir(mode=0o700)
        if layout == "empty-runs-dir":
            (store.root / "runs").mkdir(mode=0o700)
        assert scanned(store) == []
        with write_spy() as calls:
            assert executor._prior_ex10_runs_under_caller_lock(store) == []
        assert calls == []
        assert store.root.exists() is (layout != "no-evidence-root")
        assert (store.root / "runs").exists() is (layout == "empty-runs-dir")
        assert not (store.root / ".s11-custody.lock").exists()
    finally:
        remove_store(store)


def test_closed_local_tests_never_bind_an_official_start(h):
    # (5, 6, 8, 9, 33) Un LOCAL_TEST abandonado y uno terminal de diagnóstico (PASS local)
    # se ven, verificados, y no impiden el primer START OFFICIAL; ninguno cambia.
    abandoned = _closed(h, LOCAL, "abandoned")
    diagnostic = _closed(h, LOCAL, "pass")
    prior = scanned(h.store, times=3)
    seen = {(r.run_id, r.evidence_mode, r.finalized, r.decision, r.problems) for r in prior}
    assert seen == {(abandoned, LOCAL, True, "NOT_DEMONSTRATED", ()),
                    (diagnostic, LOCAL, True, "PASS", ())}  # fmt: skip
    assert executor.binding_refusal(prior, evidence_mode=OFFICIAL, acknowledged=()) == ([], False)
    keep = [run_path(h, abandoned), run_path(h, diagnostic)]
    assert start_keeping(h, keep, evidence_mode=OFFICIAL)["phase"] == "PREPARED"


def test_an_active_local_test_blocks_any_start_without_touching_it(h):
    # (7) Un LOCAL_TEST abierto impide cualquier START en la misma custodia.
    active = h.start()["run_id"]
    for mode in (LOCAL, OFFICIAL):
        rejected = start_keeping(h, [run_path(h, active)], evidence_mode=mode)
        assert rejected.reasons == (f"active-run:{active}",) and not rejected.terminal


@pytest.mark.parametrize("how", ["pass", "fail"])
def test_an_official_pass_or_fail_blocks_definitively_without_touching_it(h, how):
    # (10, 11, 34, 35) El primer run oficial PASS/FAIL es el resultado: ninguno oficial más.
    binding = _closed(h, OFFICIAL, how)
    prior = scanned(h.store)
    assert [(r.evidence_mode, r.decision, r.problems) for r in prior] == [
        (OFFICIAL, how.upper(), ())
    ]
    rejected = start_keeping(h, [run_path(h, binding)], evidence_mode=OFFICIAL)
    assert rejected.reasons == (f"binding-run-exists:{binding}",) and rejected.terminal
    assert start_keeping(h, [run_path(h, binding)], evidence_mode=LOCAL)["phase"] == "PREPARED"


def test_official_not_demonstrated_runs_need_each_exact_acknowledgement(h):
    # (12–15, 36, 37) Cada NOT_DEMONSTRATED oficial se reconoce por su run_id: ni automático,
    # ni comodín, ni uno por otro; un reconocimiento desconocido se rechaza.
    first = _closed(h, OFFICIAL, "abandoned")
    second = _closed(h, OFFICIAL, "abandoned", acknowledged_not_demonstrated=[first])
    keep = [run_path(h, first), run_path(h, second)]
    pending = {f"not-demonstrated-unacknowledged:{run}" for run in (first, second)}
    cases = [
        ((), pending),
        ((first,), {f"not-demonstrated-unacknowledged:{second}"}),
        ((second,), {f"not-demonstrated-unacknowledged:{first}"}),
        (("*",), pending | {"acknowledged-unknown:*"}),
        ((MISSING,), pending | {f"acknowledged-unknown:{MISSING}"}),
        ((first, second, MISSING), {f"acknowledged-unknown:{MISSING}"}),
    ]
    for acknowledged, reasons in cases:
        rejected = start_keeping(
            h, keep, evidence_mode=OFFICIAL, acknowledged_not_demonstrated=list(acknowledged)
        )
        assert set(rejected.reasons) == reasons and not rejected.terminal
    started = start_keeping(
        h, keep, evidence_mode=OFFICIAL, acknowledged_not_demonstrated=[first, second]
    )
    assert started["phase"] == "PREPARED"


def _official_open(h, monkeypatch, phase):
    probes = h.probes
    h.start(evidence_mode=OFFICIAL)
    if phase == "STARTED":
        monkeypatch.setattr(
            executor, "_arm_emission", lambda *a, **k: (_ for _ in ()).throw(SystemExit(137))
        )
    elif phase == "EMITTING":
        h.set_probes(generator=lambda plan, prepared, transport: _DiesOnRun())
    elif phase == "DRAINING":
        h.set_probes(database=lambda: (_ for _ in ()).throw(SystemExit(137)))
    if phase != "PREPARED":
        with pytest.raises(SystemExit):
            h.execute()
    monkeypatch.undo()
    h.probes = probes
    return h.run_id


@pytest.mark.parametrize("phase", ["PREPARED", "STARTED", "EMITTING", "DRAINING"])
def test_an_open_official_run_blocks_without_touching_it(h, monkeypatch, phase):
    # (16) Un run oficial abierto en cualquier fase impide otro START.
    active = _official_open(h, monkeypatch, phase)
    assert str(executor.phase_of(executor.load_existing_state_readonly(h.store, active))) == phase
    for mode in (LOCAL, OFFICIAL):
        rejected = start_keeping(h, [run_path(h, active)], evidence_mode=mode)
        assert rejected.reasons == (f"active-run:{active}",)


def _damage(h, run_id, damage):
    """Deja un rastro previo inverificable; devuelve las rutas que el START no debe tocar."""
    root = run_path(h, run_id)
    index = root / "metadata" / "artifacts.jsonl"
    plan = root / h.state(run_id).record(executor.ART_PLAN)["relative_path"]
    elsewhere = h.store.root.parent / "elsewhere"
    if damage == "tampered-artifact":
        raw = next((root / "raw").rglob("*.json"))
        raw.write_bytes(raw.read_bytes() + b" ")
    elif damage == "metadata-removed":
        shutil.rmtree(root / "metadata" / "EX-10")
    elif damage == "corrupt-index":
        with index.open("ab") as handle:
            handle.write(b"{not json\n")
    elif damage == "invalid-hmac":
        index.with_suffix(".jsonl.hmac").write_text("0" * 64 + "\n", encoding="ascii")
    elif damage == "missing-artifact":
        plan.unlink()
    elif damage == "symlinked-run":
        elsewhere.mkdir()
        root.rename(elsewhere / run_id)
        root.symlink_to(elsewhere / run_id, target_is_directory=True)
        return [root, elsewhere]
    elif damage == "symlinked-index":
        elsewhere.mkdir()
        index.rename(elsewhere / "artifacts.jsonl")
        index.symlink_to(elsewhere / "artifacts.jsonl")
        return [root, elsewhere]
    return [root]


DAMAGES = ["tampered-artifact", "metadata-removed", "corrupt-index", "invalid-hmac",
           "missing-artifact", "symlinked-run", "symlinked-index"]  # fmt: skip


@pytest.mark.parametrize("damage", DAMAGES)
@pytest.mark.parametrize("mode", [LOCAL, OFFICIAL])
def test_an_unverifiable_prior_trace_blocks_without_being_repaired(h, mode, damage):
    # (17–23, 38) Un rastro previo que no se puede verificar bloquea cualquier START, de
    # cualquier modo; no se repara, no se sigue un symlink y no desaparece.
    prior = _closed(h, mode, "pass")
    keep = _damage(h, prior, damage)
    found = scanned(h.store)
    assert [run.run_id for run in found] == [prior] and found[0].problems
    for evidence_mode in (LOCAL, OFFICIAL):
        rejected = start_keeping(h, keep, evidence_mode=evidence_mode)
        assert rejected.reasons == (f"prior-run-unverifiable:{prior}",)


def test_a_run_path_that_is_not_a_directory_blocks(h):
    # (24) Una ruta con nombre de run que no es un directorio no se interpreta: bloquea.
    (h.store.root / "runs").mkdir(parents=True, mode=0o700)
    (h.store.root / "runs" / MISSING).write_bytes(b"x")
    assert [(r.run_id, r.problems) for r in scanned(h.store)] == [
        (MISSING, ("run-directory-invalid",))
    ]
    rejected = start_keeping(h, [h.store.root / "runs" / MISSING])
    assert rejected.reasons == (f"prior-run-unverifiable:{MISSING}",)


def test_a_reused_run_id_is_refused_without_touching_the_existing_run(h):
    # (25) El mismo run_id se rechaza sin inicializar (ni `chmod`) el directorio existente.
    moment = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    h.set_probes(
        now=lambda: moment,
        preflight=lambda options: preflight_report(git_sha=SHA, checked_at=moment),
    )
    first = _closed(h, LOCAL, "abandoned", nonce="nonce-fixed-0001")
    rejected = start_keeping(h, [run_path(h, first)], nonce="nonce-fixed-0001")
    assert rejected.reasons == ("run-id-reused",) and rejected.terminal
    # Un rastro sin índice (un START que murió al crear el directorio) también cuenta.
    other = start_keeping(h, [run_path(h, first)], nonce="nonce-fixed-0002")["run_id"]
    h.finalize(other, abandon_reason="operator-stop")
    shutil.rmtree(run_path(h, other))
    run_path(h, other).mkdir()
    rejected = start_keeping(h, [run_path(h, first), run_path(h, other)], nonce="nonce-fixed-0002")
    assert rejected.reasons == ("run-id-reused",) and rejected.terminal


def _flock_calls(monkeypatch):
    calls = []
    original = fcntl.flock

    def counting(fd, operation):
        calls.append(operation)
        return original(fd, operation)

    monkeypatch.setattr(fcntl, "flock", counting)
    return calls


def test_start_scans_under_its_own_lock_without_nesting_or_deadlock(h, monkeypatch):
    # (26, 27) START ya tiene el lock exclusivo: la exploración no pide otro (ni compartido)
    # y termina; dentro de `store.mutation()` el lector no llama a `flock`.
    prior = [_closed(h, LOCAL, "abandoned") for _ in range(3)]
    flocks = _flock_calls(monkeypatch)
    done = {}
    thread = threading.Thread(
        target=lambda: done.update(result=start_keeping(h, [run_path(h, r) for r in prior]))
    )
    thread.start()
    thread.join(30)
    assert not thread.is_alive() and done["result"]["phase"] == "PREPARED"
    assert not any(operation & fcntl.LOCK_SH for operation in flocks)
    flocks.clear()
    with h.store.mutation():
        before = len(flocks)
        assert [r.run_id for r in executor._prior_ex10_runs_under_caller_lock(h.store)] == sorted(
            [*prior, h.run_id]
        )
        assert len(flocks) == before


def test_the_public_scan_takes_one_shared_lock_for_all_runs(h, monkeypatch):
    # (28, 31) Fuera de START: un único `LOCK_SH` para toda la exploración, no uno por run.
    for _ in range(4):
        _closed(h, LOCAL, "abandoned")
    flocks = _flock_calls(monkeypatch)
    assert len(scanned(h.store)) == 4
    assert [op for op in flocks if op & fcntl.LOCK_SH] == [fcntl.LOCK_SH | fcntl.LOCK_NB]


def test_a_held_mutation_lock_makes_the_scan_a_read_race_and_start_wait(h, monkeypatch):
    # (29) Con el lock exclusivo de otro: la exploración pública es `status-read-race` sin
    # tocar nada, y START espera al lock (canónico) y después explora sin cambiar el previo.
    prior = _closed(h, LOCAL, "abandoned")
    monkeypatch.setattr(executor, "STATUS_LOCK_ATTEMPTS", 3)
    monkeypatch.setattr(executor, "STATUS_LOCK_PAUSE", 0.01)
    release = threading.Event()
    holder = _holding_the_custody_lock(h.store, release)
    try:
        before = tree(h.store.root.parent)
        with write_spy() as calls:
            rejected = refusal(executor.prior_ex10_runs, h.store)
        assert rejected.reasons == ("status-read-race",) and not rejected.terminal
        assert calls == [] and tree(h.store.root.parent) == before
        done = {}
        starter = threading.Thread(
            target=lambda: done.update(result=start_keeping(h, [run_path(h, prior)]))
        )
        starter.start()
        starter.join(0.3)
        assert starter.is_alive() and not done
    finally:
        release.set()
        holder.join()
    starter.join(30)
    assert done["result"]["phase"] == "PREPARED"


def test_concurrent_public_scans_agree_and_change_nothing(h):
    # (30)
    for _ in range(2):
        _closed(h, LOCAL, "abandoned")
    expected = scanned(h.store)
    base = h.store.root.parent
    before = tree(base)
    with write_spy() as calls:
        results, errors = _concurrently(
            8, lambda: [executor.prior_ex10_runs(h.store) for _ in range(5)]
        )
    assert errors == [] and calls == [] and tree(base) == before
    assert [scan for batch in results for scan in batch] == [expected] * 40


def test_the_public_scan_refuses_a_concurrent_write(h, monkeypatch):
    # Un índice que cambia durante la exploración (sin lock) no compone un estado.
    prior = _closed(h, LOCAL, "abandoned")
    original = executor._prior_ex10_runs_under_caller_lock

    def touched(store):
        os.utime(run_path(h, prior) / "metadata" / "artifacts.jsonl")
        return original(store)

    monkeypatch.setattr(executor, "_prior_ex10_runs_under_caller_lock", touched)
    rejected = refusal(executor.prior_ex10_runs, h.store)
    assert rejected.reasons == ("status-read-race",) and not rejected.terminal


def test_the_start_record_keeps_the_prior_scan_schema(h):
    # (18) El START custodia la exploración con el mismo schema de siempre.
    prior = _closed(h, LOCAL, "abandoned")
    h.start(evidence_mode=OFFICIAL)
    start = executor.load_existing_state_readonly(h.store, h.run_id).payload(executor.ART_START)
    assert start["prior_ex10_runs"] == [{
        "run_id": prior, "evidence_mode": LOCAL, "finalized": True,
        "decision": "NOT_DEMONSTRATED", "problems": [],
    }]  # fmt: skip


def test_the_readonly_loader_outside_start_takes_the_shared_lock(h, monkeypatch):
    # (28) Fuera de START el loader lee bajo el lock compartido: un `LOCK_SH`, y con el
    # exclusivo de otro, `status-read-race` sin tocar nada.
    h.start()
    flocks = _flock_calls(monkeypatch)
    assert executor.load_existing_state_readonly(h.store, h.run_id) is not None
    assert [op for op in flocks if op & fcntl.LOCK_SH] == [fcntl.LOCK_SH | fcntl.LOCK_NB]
    monkeypatch.setattr(executor, "STATUS_LOCK_ATTEMPTS", 3)
    monkeypatch.setattr(executor, "STATUS_LOCK_PAUSE", 0.01)
    release = threading.Event()
    holder = _holding_the_custody_lock(h.store, release)
    try:
        before = tree(h.store.root.parent)
        with write_spy() as calls:
            rejected = refusal(executor.load_existing_state_readonly, h.store, h.run_id)
        assert rejected.reasons == ("status-read-race",) and not rejected.terminal
        assert calls == [] and tree(h.store.root.parent) == before
    finally:
        release.set()
        holder.join()


def test_a_symlinked_index_is_never_read_through(h):
    # (23) El índice (o `metadata/`) a través de un symlink no se lee: status da un error
    # saneado y la exploración lo deja como ilegible, que bloquea; nada cambia.
    prior = _closed(h, LOCAL, "abandoned")
    keep = _damage(h, prior, "symlinked-index")
    base = h.store.root.parent
    before = tree(base)
    with write_spy() as calls, pytest.raises(OSError):
        executor.run_status(h.store, prior)
    with pytest.raises(OSError):
        executor._read_existing_state_under_caller_lock(h.store, prior)
    assert calls == [] and tree(base) == before
    assert [(r.run_id, r.problems) for r in scanned(h.store)] == [(prior, ("records-unreadable",))]
    assert start_keeping(h, keep).reasons == (f"prior-run-unverifiable:{prior}",)


# --- reconcile estrictamente read-only --------------------------------------------------------
# La reconciliación provisional lee el run con el loader read-only (lock compartido breve),
# verifica la custodia con `verify_readonly` y lee la base en una transacción READ ONLY: ningún
# byte ni campo de metadata cambia (ctime incluido), nada se publica ni se guarda.


def legacy_reconcile(store, run_id, git_sha, probes):
    """El `reconcile_run` anterior (abre el run con `load_state`): oráculo de la salida."""
    state = executor._open_state(store, run_id, git_sha)
    executor._require_phase(state, "reconcile")
    assessment = executor.assess(
        state, probes=probes, dataset=probes.dataset(), custody_errors=store.verify(run_id)
    )
    result = assessment.evaluation.result
    return {
        "run_id": run_id,
        "provisional": True,
        "decision": str(result.decision),
        "criteria": dict(result.criteria),
        "invalidity_reasons": list(result.invalidity_reasons),
        "not_demonstrable": list(result.not_demonstrable),
        "failed_criteria": list(result.failed_criteria),
    }


@contextmanager
def no_side_effects():
    """Además del `write_spy`: ninguna tarea Celery, escritura de storage ni guardado de modelo."""
    from celery import Celery
    from celery.app.task import Task
    from django.core.files.storage import Storage
    from django.db.models.signals import post_delete, post_save, pre_delete, pre_save

    events: list[str] = []

    def blocked(name):
        def call(*args, **kwargs):
            events.append(name)
            raise AssertionError(f"reconcile no puede llamar {name}")

        return call

    def saved(sender, **kwargs):
        events.append(f"model:{sender.__name__}")

    signals = (pre_save, post_save, pre_delete, post_delete)
    with write_spy() as calls, pytest.MonkeyPatch.context() as mp:
        mp.setattr(Task, "apply_async", blocked("celery.apply_async"))
        mp.setattr(Celery, "send_task", blocked("celery.send_task"))
        mp.setattr(Storage, "save", blocked("storage.save"))
        for signal_ in signals:
            signal_.connect(saved, weak=False, dispatch_uid="reconcile-spy")
        try:
            yield calls, events
        finally:
            for signal_ in signals:
                signal_.disconnect(dispatch_uid="reconcile-spy")


def readonly_reconcile(h, run_id=None, *, times=1):
    base_dir = h.store.root.parent
    before = tree(base_dir)
    with no_side_effects() as (calls, events):
        results = [h.reconcile(run_id) for _ in range(times)]
    assert calls == [] and events == []
    assert tree(base_dir) == before
    assert all(result == results[0] for result in results)
    return results[0]


def _reconciling(h, how="pass"):
    if how == "fail":
        h.backend.modes[h.plan.for_vu(4)[3].request_key] = "500"
    h.start()
    h.execute()
    h.backend.modes.clear()
    return h.run_id


@pytest.mark.parametrize("how", ["pass", "fail", "decided"])
def test_reconcile_is_strictly_read_only_and_matches_the_previous_path(h, monkeypatch, how):
    # (1–4, 14–16, 20) Misma salida que el camino anterior; ni un byte ni un ctime cambian.
    if how == "decided":
        _reach(h, monkeypatch, "DECIDED")
    else:
        _reconciling(h, how)
    result = readonly_reconcile(h)
    assert result["provisional"] is True
    assert result["decision"] == {"pass": "PASS", "fail": "FAIL", "decided": "PASS"}[how]
    assert result == legacy_reconcile(h.store, h.run_id, SHA, h.probes)


def test_the_previous_reconcile_path_touched_ctime_and_the_new_one_does_not(h):
    # (control negativo) El camino anterior (`load_state`) cambia el ctime de directorios del
    # run sin tocar bytes; el nuevo, nada.
    _reconciling(h)
    base_dir = h.store.root.parent
    before = tree(base_dir)
    legacy_reconcile(h.store, h.run_id, SHA, h.probes)
    after = tree(base_dir)
    changed = {path: [k for k in before[path] if before[path][k] != after[path][k]]
               for path in before if before[path] != after[path]}  # fmt: skip
    assert changed and all(set(fields) == {"ctime"} for fields in changed.values())
    assert all(before[path]["type"] == stat.S_IFDIR for path in changed)
    readonly_reconcile(h)


def test_one_hundred_reconciliations_change_nothing(h):
    # (5)
    _reconciling(h)
    assert readonly_reconcile(h, times=100)["decision"] == "PASS"


def test_concurrent_reconciliations_agree_and_change_nothing(h):
    # (6)
    _reconciling(h)
    expected = readonly_reconcile(h)
    base_dir = h.store.root.parent
    before = tree(base_dir)
    with write_spy() as calls:
        results, errors = _concurrently(4, lambda: [h.reconcile() for _ in range(3)])
    assert errors == [] and calls == [] and tree(base_dir) == before
    assert [r for batch in results for r in batch] == [expected] * 12


@pytest.mark.parametrize("where", ["index", "stream"])
def test_a_write_during_reconciliation_is_a_read_race(h, monkeypatch, where):
    # (7) El índice o un stream cambian mientras se reconcilia: no se mezcla, se rechaza.
    _reconciling(h)
    run_root = run_path(h, h.run_id)
    target = (run_root / "metadata" / "artifacts.jsonl" if where == "index"
              else run_root / "logs" / "EX-10" / "http.jsonl")  # fmt: skip
    original = executor.assess

    def touched(*args, **kwargs):
        os.utime(target)
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "assess", touched)
    rejected = refusal(h.reconcile)
    assert rejected.reasons == ("reconcile-read-race",) and not rejected.terminal


def test_a_held_mutation_lock_makes_reconcile_a_read_race_without_writing(h, monkeypatch):
    _reconciling(h)
    monkeypatch.setattr(executor, "STATUS_LOCK_ATTEMPTS", 3)
    monkeypatch.setattr(executor, "STATUS_LOCK_PAUSE", 0.01)
    release = threading.Event()
    holder = _holding_the_custody_lock(h.store, release)
    try:
        before = tree(h.store.root.parent)
        with write_spy() as calls:
            rejected = refusal(h.reconcile)
        assert rejected.reasons == ("reconcile-read-race",) and not rejected.terminal
        assert calls == [] and tree(h.store.root.parent) == before
    finally:
        release.set()
        holder.join()


@pytest.mark.parametrize("run_id", [MISSING, "s11-x", "../runs"])
def test_reconcile_of_a_missing_run_creates_nothing(h, run_id):
    # (8)
    _reconciling(h)
    before = tree(h.store.root.parent)
    with write_spy() as calls:
        rejected = refusal(h.reconcile, run_id)
    assert rejected.reasons == ("run-not-found",) and rejected.terminal
    assert calls == [] and tree(h.store.root.parent) == before


@pytest.mark.parametrize(
    "phase",
    ["CREATED", "PREPARED", "STARTED", "EMITTING", "STOPPED_PRE_T0", "DRAINING",
     "ABANDONED_PRE_T0", "NOT_DEMONSTRATED", "FINALIZED_PASS", "FINALIZED_FAIL"],
)  # fmt: skip
def test_reconcile_in_another_phase_is_refused_without_writing(h, monkeypatch, phase):
    # (10) Las fases permitidas siguen siendo RECONCILING y DECIDED.
    _reach(h, monkeypatch, phase)
    before = tree(h.store.root.parent)
    with write_spy() as calls, pytest.raises(executor.Ex10ExecutorError):
        h.reconcile()
    assert calls == [] and tree(h.store.root.parent) == before


@pytest.mark.parametrize(
    "damage", ["tampered-artifact", "invalid-hmac", "missing-artifact", "corrupt-index",
               "metadata-removed", "symlinked-index", "symlinked-run"],
)  # fmt: skip
def test_reconcile_of_corrupt_evidence_never_repairs_and_never_passes(h, damage):
    # (9–11) Un rechazo saneado o un NOT_DEMONSTRATED provisional; nunca PASS ni reparación.
    run_id = _reconciling(h)
    _damage(h, run_id, damage)
    base_dir = h.store.root.parent
    before = tree(base_dir)
    with write_spy() as calls:
        try:
            result = h.reconcile()
        except (executor.Ex10ExecutorError, ValueError, OSError):
            result = None
    assert calls == [] and tree(base_dir) == before
    assert result is None or result["decision"] == "NOT_DEMONSTRATED"


@pytest.mark.parametrize("missing", ["database", "telemetry", "broker"])
def test_reconcile_without_db_telemetry_or_broker_is_not_demonstrated(h, missing):
    # (12, 13) Una fuente no disponible: NOT_DEMONSTRATED provisional, sin mutar nada.
    _reconciling(h)
    if missing == "database":
        h.set_probes(database=lambda: (_ for _ in ()).throw(DatabaseError("down")))
    elif missing == "broker":
        h.set_probes(broker_dynamic=lambda: (_ for _ in ()).throw(ConnectionError("down")))
    else:
        stream = run_path(h, h.run_id) / "logs" / "EX-10" / "http.jsonl"
        stream.rename(stream.with_name("http.jsonl.unavailable"))
    assert readonly_reconcile(h)["decision"] == "NOT_DEMONSTRATED"


@pytest.mark.django_db
def test_the_database_snapshot_only_reads_inside_a_read_only_transaction():
    # (9, 14) La lectura de la base de reconcile: sólo SELECT dentro de `READ ONLY`.
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.experiments_app import ex10_reconcile

    with CaptureQueriesContext(connection) as queries:
        ex10_reconcile.snapshot_database()
    statements = [q["sql"].lstrip().split()[0].upper() for q in queries.captured_queries]
    assert "SET" in statements
    assert set(statements) <= {"SET", "SELECT", "SAVEPOINT", "RELEASE", "ROLLBACK"}


def test_the_cli_reconcile_is_strictly_read_only(h, cli, capsys):
    # (17–19) La CLI imprime el JSON canónico de la reconciliación, sin escribir nada.
    _reconciling(h)
    expected = readonly_reconcile(h)
    base_dir = h.store.root.parent
    before = tree(base_dir)
    capsys.readouterr()
    with write_spy() as calls:
        printed = cli("reconcile", "--run-id", h.run_id, "--expected-git-sha", SHA)
    assert printed == expected and calls == [] and tree(base_dir) == before
    assert capsys.readouterr().err == ""


def test_reconcile_never_follows_a_symlinked_run(h):
    # Un run que es un symlink no es un run: `run-not-found`, sin leer a través de él.
    run_id = _reconciling(h)
    _damage(h, run_id, "symlinked-run")
    before = tree(h.store.root.parent)
    with write_spy() as calls:
        rejected = refusal(h.reconcile)
    assert rejected.reasons == ("run-not-found",) and rejected.terminal
    assert calls == [] and tree(h.store.root.parent) == before


def test_reconcile_reports_a_missing_non_json_artifact_as_custody(h):
    # Un artefacto custodiado que falta (y que no es un payload JSON) llega a la custodia:
    # NOT_DEMONSTRATED provisional con `custody:missing:`; nunca se repara.
    run_id = _reconciling(h)
    record = h.store.persist_bytes(
        run_id=run_id, scenario_id="EX-10", artifact_type="ex10-test-note",
        kind=executor.ArtifactKind.LOG, classification=executor.Classification.INTERNAL,
        filename="note.txt", content=b"note", mime_type="text/plain", source="test",
    )  # fmt: skip
    (run_path(h, run_id) / record.relative_path).unlink()
    result = readonly_reconcile(h)
    assert result["decision"] == "NOT_DEMONSTRATED"
    assert f"custody:missing:{record.artifact_id}" in result["invalidity_reasons"]
    assert not (run_path(h, run_id) / record.relative_path).exists()
