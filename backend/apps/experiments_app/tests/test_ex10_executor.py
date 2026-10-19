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


def test_the_first_start_is_allowed(native_h):
    h = native_h
    # (94)
    assert h.start(evidence_mode=OFFICIAL)["phase"] == "PREPARED"
    start = h.payload(executor.ART_START)
    assert (start["evidence_mode"], start["prior_ex10_runs"]) == (OFFICIAL, [])


@pytest.mark.parametrize(("mode", "decision"), [(None, "PASS"), ("500", "FAIL")])
def test_an_official_pass_or_fail_binds_ex10(native_history_h, mode, decision):
    h = native_history_h
    # (96, 97) Ni un run oficial más, ni reconociendo el anterior.
    if mode:
        h.backend.modes[h.plan.items[0].request_key] = mode
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    final = h.finalize()
    assert (final["decision"], final["binding"]) == (decision, True)
    reasons, terminal = native_binding_rejection(h, acknowledged=[h.run_id])
    assert terminal
    assert f"binding-run-exists:{h.run_id}" in reasons
    assert len(list((h.store.root / "runs").iterdir())) == 1


@pytest.mark.parametrize("case", ["missing", "unknown", "exact"])
def test_a_not_demonstrated_run_needs_an_explicit_acknowledgement(native_history_h, case):
    h = native_history_h
    # (95) Nunca automáticamente: el operador reconoce el run_id NOT_DEMONSTRATED.
    h.set_probes(issue_tokens=lambda result: (_ for _ in ()).throw(KeyboardInterrupt))
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    h.finalize(abandon_reason="pre-t0-stop")
    previous = h.run_id
    if case == "missing":
        reasons, terminal = native_binding_rejection(h)
        assert reasons == [f"not-demonstrated-unacknowledged:{previous}"]
        assert not terminal
    elif case == "unknown":
        unknown = "s11-99999999-9999-5999-8999-999999999999"
        reasons, terminal = native_binding_rejection(h, acknowledged=[previous, unknown])
        assert reasons == [f"acknowledged-unknown:{unknown}"] and not terminal
    else:
        h.start(evidence_mode=OFFICIAL, acknowledged_not_demonstrated=[previous])
        assert h.run_id != previous
        assert h.payload(executor.ART_START)["acknowledged_not_demonstrated"] == [previous]


def test_an_active_run_blocks_any_other_start(native_history_h):
    h = native_history_h
    # (99) Dos runs activos, nunca; tampoco uno oficial junto a uno de test.
    h.start(evidence_mode=LOCAL)
    for mode in (LOCAL, OFFICIAL):
        assert refusal(h.start, evidence_mode=mode).reasons == (f"active-run:{h.run_id}",)


def test_an_unverifiable_prior_run_blocks_the_start(native_history_h):
    h = native_history_h
    h.start()
    h.execute()
    h.finalize()
    raw = next((h.state().run_root / "raw").rglob("*.json"))
    raw.write_bytes(raw.read_bytes().replace(b'"send_count":1', b'"send_count":0', 1))
    rejected = refusal(h.start, evidence_mode=OFFICIAL)
    assert rejected.reasons == (f"prior-run-unverifiable:{h.run_id}",)


def test_a_prior_run_cannot_be_hidden_by_removing_its_metadata(native_history_h):
    h = native_history_h
    # §41: cualquier rastro de EX-10 en la custodia cuenta como run previo.
    h.start(evidence_mode=OFFICIAL)
    h.execute()
    h.finalize()
    import shutil

    shutil.rmtree(h.state().run_root / "metadata" / "EX-10")
    found = executor.prior_ex10_runs(h.store)
    reasons, terminal = executor.binding_refusal(found, evidence_mode=OFFICIAL, acknowledged=())
    assert reasons == [f"prior-run-unverifiable:{h.run_id}"] and not terminal
    native_damaged_custody_rejection(
        h, prior=h.run_id, damage="metadata-removed", keep=[run_path(h, h.run_id)]
    )


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

    h.set_probes(database=broken, broker_dynamic=broken, post_run_broker=lambda plan: broken())
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
    assert (
        h.payload(executor.ART_MANIFEST_INITIAL)["experiment_id"] == executor.EXPERIMENT_IDS[LOCAL]
    )
    assert executor.EXPERIMENT_IDS[LOCAL] != executor.EXPERIMENT_IDS[OFFICIAL]
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


def test_rehearsal_refuses_an_official_run_without_writing(native_history_h, no_emission):
    h = native_history_h
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


def test_started_rehearsal_is_visible_in_status_and_abandons_before_t0(
    native_history_h, no_emission
):
    h = native_history_h
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


def test_the_cli_refuses_rehearsal_on_an_official_run(native_history_h, cli, capsys):
    h = native_history_h
    started = h.start(evidence_mode=OFFICIAL)
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


def test_closed_local_tests_never_bind_an_official_start(native_history_h):
    h = native_history_h
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


def test_an_active_local_test_blocks_any_start_without_touching_it(native_history_h):
    h = native_history_h
    # (7) Un LOCAL_TEST abierto impide cualquier START en la misma custodia.
    active = h.start()["run_id"]
    for mode in (LOCAL, OFFICIAL):
        rejected = start_keeping(h, [run_path(h, active)], evidence_mode=mode)
        assert rejected.reasons == (f"active-run:{active}",) and not rejected.terminal


@pytest.mark.parametrize("how", ["pass", "fail"])
def test_an_official_pass_or_fail_blocks_definitively_without_touching_it(native_history_h, how):
    h = native_history_h
    # (10, 11, 34, 35) El primer run oficial PASS/FAIL es el resultado: ninguno oficial más.
    binding = _closed(h, OFFICIAL, how)
    prior = scanned(h.store)
    assert [(r.evidence_mode, r.decision, r.problems) for r in prior] == [
        (OFFICIAL, how.upper(), ())
    ]
    reasons, terminal = native_binding_rejection(h, keep=[run_path(h, binding)])
    assert reasons == [f"binding-run-exists:{binding}"] and terminal
    assert start_keeping(h, [run_path(h, binding)], evidence_mode=LOCAL)["phase"] == "PREPARED"


@pytest.mark.parametrize(
    "case", ["none", "first", "second", "wildcard", "unknown", "extra", "exact"]
)
def test_official_not_demonstrated_runs_need_each_exact_acknowledgement(native_history_h, case):
    h = native_history_h
    # (12–15, 36, 37) Cada NOT_DEMONSTRATED oficial se reconoce por su run_id: ni automático,
    # ni comodín, ni uno por otro; un reconocimiento desconocido se rechaza.
    first = _closed(h, OFFICIAL, "abandoned")
    second = _closed(h, OFFICIAL, "abandoned", acknowledged_not_demonstrated=[first])
    keep = [run_path(h, first), run_path(h, second)]
    pending = {f"not-demonstrated-unacknowledged:{run}" for run in (first, second)}
    cases = {
        "none": ((), pending),
        "first": ((first,), {f"not-demonstrated-unacknowledged:{second}"}),
        "second": ((second,), {f"not-demonstrated-unacknowledged:{first}"}),
        "wildcard": (("*",), pending | {"acknowledged-unknown:*"}),
        "unknown": ((MISSING,), pending | {f"acknowledged-unknown:{MISSING}"}),
        "extra": ((first, second, MISSING), {f"acknowledged-unknown:{MISSING}"}),
    }
    if case == "exact":
        started = start_keeping(
            h, keep, evidence_mode=OFFICIAL, acknowledged_not_demonstrated=[first, second]
        )
        assert started["phase"] == "PREPARED"
    else:
        acknowledged, expected = cases[case]
        reasons, terminal = native_binding_rejection(h, acknowledged=acknowledged, keep=keep)
        assert set(reasons) == expected and not terminal


def _official_open(h, monkeypatch, phase):
    probes = h.probes
    h.start(evidence_mode=OFFICIAL)
    with monkeypatch.context() as crash_patch:
        if phase == "STARTED":
            crash_patch.setattr(
                executor, "_arm_emission", lambda *a, **k: (_ for _ in ()).throw(SystemExit(137))
            )
        elif phase == "EMITTING":
            h.set_probes(generator=lambda plan, prepared, transport: _DiesOnRun())
        elif phase == "DRAINING":
            h.set_probes(database=lambda: (_ for _ in ()).throw(SystemExit(137)))
        if phase != "PREPARED":
            with pytest.raises(SystemExit):
                h.execute()
    h.probes = probes
    return h.run_id


@pytest.mark.parametrize("phase", ["PREPARED", "STARTED", "EMITTING", "DRAINING"])
def test_an_open_official_run_blocks_without_touching_it(native_history_h, monkeypatch, phase):
    h = native_history_h
    # (16) Un run oficial abierto en cualquier fase impide otro START.
    active = _official_open(h, monkeypatch, phase)
    assert str(executor.phase_of(executor.load_existing_state_readonly(h.store, active))) == phase
    rejected = start_keeping(h, [run_path(h, active)], evidence_mode=LOCAL)
    assert rejected.reasons == (f"active-run:{active}",)
    reasons, terminal = native_binding_rejection(h, keep=[run_path(h, active)])
    assert reasons == [f"active-run:{active}"] and not terminal


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
def test_an_unverifiable_prior_trace_blocks_without_being_repaired(native_history_h, mode, damage):
    h = native_history_h
    # (17–23, 38) Un rastro previo que no se puede verificar bloquea cualquier START, de
    # cualquier modo; no se repara, no se sigue un symlink y no desaparece.
    prior = _closed(h, mode, "pass")
    keep = _damage(h, prior, damage)
    found = scanned(h.store)
    assert [run.run_id for run in found] == [prior] and found[0].problems
    for evidence_mode in (LOCAL, OFFICIAL):
        reasons, terminal = executor.binding_refusal(
            found, evidence_mode=evidence_mode, acknowledged=()
        )
        assert reasons == [f"prior-run-unverifiable:{prior}"] and not terminal
        if mode == OFFICIAL and evidence_mode == OFFICIAL:
            native_damaged_custody_rejection(h, prior=prior, damage=damage, keep=keep)
        else:
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


def test_the_start_record_keeps_the_prior_scan_schema(native_history_h):
    h = native_history_h
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
        down = ConnectionError("down")
        h.set_probes(
            broker_dynamic=lambda: (_ for _ in ()).throw(down),
            post_run_broker=lambda plan: (_ for _ in ()).throw(down),
        )
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


def test_v2_direct_official_without_bridge_refused_before_preflight_or_run_write(h):
    before = {
        str(p.relative_to(h.store.root)): sha256_bytes(p.read_bytes())
        for p in h.store.root.rglob("*")
        if p.is_file()
    }
    calls = []
    original = h.probes.preflight

    def observed(options):
        calls.append("preflight")
        return original(options)

    h.probes.preflight = observed
    with pytest.raises(executor.Ex10Refusal) as error:
        h.start(evidence_mode=OFFICIAL)
    assert error.value.terminal
    assert "operational-context-required" in error.value.reasons
    assert calls == []
    assert before == {
        str(p.relative_to(h.store.root)): sha256_bytes(p.read_bytes())
        for p in h.store.root.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("microseconds", [0, 1, 999999])
def test_v2_pure_proposal_uses_original_six_field_uuid5_and_frozen_timestamp(
    plan, microseconds, monkeypatch
):
    instant = datetime(2026, 10, 5, 19, 0, 0, microseconds, tzinfo=UTC)
    nonce = "1" * 64

    def forbidden(*args, **kwargs):
        raise AssertionError("pure proposal performed IO or sampled the clock")

    monkeypatch.setattr(executor, "create_manifest", forbidden)
    monkeypatch.setattr(executor, "_now", forbidden)
    value = executor.propose_run(started_at=instant, git_sha=SHA, nonce=nonce)
    from apps.experiments_app.manifest import make_run_id

    expected = make_run_id(
        started_at=instant,
        git_sha=SHA,
        dataset_id=ex10.DATASET_ID,
        scenario_id=ex10.SCENARIO_ID,
        seed=ex10.DATASET_SEED,
        execution_nonce=nonce,
    )
    assert value["run_id"] == expected
    assert value["started_at"] == instant.isoformat(timespec="microseconds").replace("+00:00", "Z")
    assert len(value) == 8 and value["identity_digest"] == sha256_bytes(
        canonical_json_bytes({k: v for k, v in value.items() if k != "identity_digest"})
    )
    assert executor.propose_run(started_at=instant, git_sha=SHA, nonce=nonce) == value
    from apps.experiments_app import ex10_bridge_protocol as wire

    assert wire.qualify_run_identity(value) == value


@contextmanager
def v2_native_host_material(*, history=("ND",), evidence_root=None):
    """Reuse frozen actual B1/B2/B3/B4 CAS fixtures; no fabricated host verdict."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "w08_native_host_fixture", Path(__file__).with_name("test_ex10_operational_context.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    session = module.native_session.__wrapped__(evidence_root=evidence_root)
    rig = pairs = None
    try:
        native = next(session)
        rig = module.native_start_rig.__wrapped__(native)
        # Select only the initial synthetic B3 history at fixture construction.
        # The frozen native fixture and all production validators stay intact.
        original = module.cf.rig.__wrapped__
        module.cf.rig.__wrapped__ = lambda request: original(SimpleNamespace(param=history))
        try:
            opening = next(rig)
        finally:
            module.cf.rig.__wrapped__ = original
        pairs = module.pair.__wrapped__()
        pair = next(pairs)
        source = module.native_sources.__wrapped__(opening, pair)
        yield SimpleNamespace(module=module, native=native, rig=opening, source=source)
    finally:
        for generator in (pairs, rig, session):
            if generator is not None:
                generator.close()


@pytest.mark.parametrize("fixture_number", range(3))
def test_v2_native_preflight_store_closes_its_descriptors_after_fixture_scope(
    dataset, fixture_number
):
    import errno

    with v2_native_host_material(history=()) as fixture:
        rig = v2_native_start_setup(fixture, dataset)
        binding = rig.broker.sources.registry.bindings["preflight"]
        store = binding.store
        reference = json.loads(binding.reference)
        descriptor = store.fd
        assert store.read(reference["path"]) == rig.context.preflight
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
        rig.context.channel.close()
        rig.host.close()
    # Releasing the whole fixture must release its retained filesystem lease.
    # Repetition detects leaked leases across independent fixture lifetimes.
    with pytest.raises(OSError) as error:
        os.fstat(descriptor)
    assert error.value.errno == errno.EBADF


@pytest.fixture
def v2_native_host():
    with v2_native_host_material() as host:
        yield host


@pytest.fixture
def v2_empty_native_host():
    with v2_native_host_material(history=()) as host:
        yield host


@pytest.fixture
def native_h(h, v2_empty_native_host, dataset, monkeypatch):
    """Scientific regressions on actual native 9/5-frame admission and empty B3.

    OS/stack providers are explicit synthetic fixtures. Finalize prepares a real
    seal only; this harness does not claim host publication or slot retirement.
    It cannot make a second START and never supplies a fake authority verdict.
    """
    old_store, old_backend_store = h.store, h.backend.store
    started = v2_native_start_setup(v2_empty_native_host, dataset, client_proofs=True)
    h.probes = replace(h.probes, fingerprint=started.probes.fingerprint)
    h.store = h.backend.store = started.store
    h.native_started = started

    def start(**kwargs):
        assert not h.run_ids, "this scientific harness admits exactly one START"
        assert kwargs.pop("evidence_mode", OFFICIAL) == OFFICIAL
        assert not kwargs, "unsupported native scientific test options"
        v2_client_proof_start_setup(started, monkeypatch)
        result, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
        assert not errors and len(responses) == 1
        h.run_ids.append(result["run_id"])
        return result

    def step(action, operation, run_id=None, **kwargs):
        assert run_id is None or run_id == h.run_id

        def make_guard(current):
            def guarded(**kw):
                if kw.get("phase") == "BARRIER":
                    # Test-only HOST publication follows the actual ARMED artifact.
                    # Current full native GOV and independently signed B3/B4 heads
                    # remain necessary; no PF refresh, extra RPC or boolean verdict.
                    source, a = single.broker.sources, single.fixture.rig.a
                    accepted = source.read_authority()
                    challenge = single.context.host_challenge.to_dict()["challenge"]
                    record = single.context.reservation
                    directory = single.store.root / ".s11-operational" / proof.exchange_id
                    for role, raw in (
                        ("emission-material", accepted.canonical_material),
                        (
                            "emission-head",
                            canonical_json_bytes(source.publisher.transport.head_b3(challenge)),
                        ),
                        (
                            "emission-coordination",
                            canonical_json_bytes(a.transport.head(challenge)),
                        ),
                        (
                            "emission-receipt",
                            a.journal.receipt_store.read(
                                record["reservation_receipt_reference"]["path"]
                            ),
                        ),
                    ):
                        fd = os.open(
                            directory / (role + ".json"),
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                        )
                        try:
                            assert os.write(fd, raw) == len(raw)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                return current(**kw)

            return guarded

        if action == "finalize":

            def prepare(*, context, store, run_id, result):
                assert context is single.context and store is h.store
                assert run_id == h.run_id == result["run_id"]
                state = executor.load_existing_state_readonly(store, run_id)
                assert state is not None and state.finalized
                executor.validate_finalized_state(
                    state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
                )
                source = single.broker.sources
                actual = source.read_authority()
                assert (
                    single.fixture.module.op.history.find_run(actual, run_id)["lifecycle_state"]
                    == "OPEN"
                )
                assert (
                    single.fixture.rig.a.journal.read()["latest"][
                        context.reservation["reservation_id"]
                    ]["state"]
                    == "COMMITTED"
                )

        single = v2_native_single_setup(
            h.native_started,
            action=action,
            client_proofs=True,
            guard_factory=make_guard,
            terminal_preparer=prepare if action == "finalize" else None,
        )
        proof, _ = v2_client_proof_single_setup(single, monkeypatch)
        result, responses = v2_run_single_host(
            single,
            lambda: operation(
                h.store,
                run_id=h.run_id,
                git_sha=SHA,
                probes=h.probes,
                operational_context=single.context,
                **kwargs,
            ),
        )
        assert len(responses) == 1
        return result

    monkeypatch.setattr(h, "start", start)
    monkeypatch.setattr(
        h, "execute", lambda run_id=None: step("execute", executor.execute_run, run_id)
    )
    monkeypatch.setattr(
        h, "drain", lambda run_id=None: step("drain", executor.run_drain_step, run_id)
    )
    monkeypatch.setattr(
        h, "reconcile", lambda run_id=None: step("reconcile", executor.reconcile_run, run_id)
    )
    monkeypatch.setattr(
        h,
        "finalize",
        lambda run_id=None, **kw: step("finalize", executor.finalize_run, run_id, **kw),
    )
    try:
        yield h
    finally:
        started.context.channel.close()
        started.host.close()
        h.store, h.backend.store = old_store, old_backend_store


def _v2_client_proof_original_bindings(*, store, channel, fixture, source, clock):
    """Construct actual copied-proof providers before context captures identity."""
    a, mod = fixture.rig.a, fixture.module
    snapshot = source.read_authority()
    receipts = mod.op.history._receipts(snapshot.material["state"])
    proof = executor.ExecutorHostProofs(
        root=str(store.root),
        exchange_id=channel.exchange_id,
        b3_pins=[a.f.pin.record],
        genesis_digest=sha256_bytes(canonical_json_bytes(receipts[0])),
        genesis_binding=receipts[0]["genesis"],
        clock=clock,
    )
    errors = []

    def traced_commit(**arguments):
        import traceback

        try:
            return proof.verify_commit(**arguments)
        except Exception as error:
            errors.append(
                {
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "locations": [
                        (f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)
                    ],
                }
            )
            raise

    return SimpleNamespace(
        proof=proof,
        guard=proof.current,
        prior_reader=proof.common_prior,
        prior_verifier=proof.verify_common,
        verify_commit=traced_commit,
        proof_errors=errors,
    )


def v2_native_start_setup(
    v2_native_host,
    dataset,
    *,
    preflight_age=0,
    execution_nonce="d" * 64,
    acknowledgments=None,
    client_proofs=False,
    guard_factory=None,
    channel_guard_factory=None,
):
    """Real client and host use one exact internally produced private PF file."""
    import uuid

    from apps.experiments_app import ex10_bridge_protocol as client_wire

    fixture = v2_native_host
    x, rig, mod = fixture.source, fixture.rig, fixture.module
    a, native = rig.a, rig.n
    raw = mod.wire.parse_request_bytes(rig.raw).to_dict()
    fixed = {
        k: raw[k]
        for k in (
            "exchange_id",
            "authority_id",
            "authority_epoch",
            "authorization_reference",
            "session_id",
            "session_header_sha256",
            "runtime_profile_sha256",
            "source_revision",
            "project",
        )
    }
    fixed.update(
        bridge_key_id=a.bridge_pin.key_id,
        bridge_key_epoch=a.bridge_pin.key_epoch,
        container_identity_digest=native.options["process"].identity_digest,
        deployment_verification_sha256=native.options["process"].deployment_verification_sha256,
    )
    original_budget = 120 if client_proofs else 30
    channel_guard = None if channel_guard_factory is None else channel_guard_factory()
    assert channel_guard is None or callable(channel_guard)
    channel = executor.ExecutorBridgeChannel(
        read_fd=x.executor.read_fd,
        write_fd=x.executor.write_fd,
        exchange="START",
        exchange_id=x.host.exchange_id,
        budget=original_budget,
        process_guard=channel_guard,
    )
    x.executor.close()
    store = make_store("v2-native-" + uuid.uuid4().hex)
    store.root = Path(native.value["host_roots"]["evidence"]["path"])
    required = rig.sample["context"].preflight_required_checks
    # Keep the synthetic host's source qualification exactly explicit. These
    # existing fixtures use a supplied imported/frozen apparatus fingerprint.
    fingerprint = rig.sample["context"].executor_fingerprint
    report = preflight_report(
        git_sha=fixed["source_revision"], checked_at=a.now() - timedelta(seconds=preflight_age)
    )
    probes = executor.ExecutorProbes(
        preflight=lambda options: report,
        dataset=lambda: dataset,
        provider=lambda: [],
        sources=lambda root: [],
        fingerprint=lambda: fingerprint,
        now=a.now,
    )
    prior_view = []
    material = mod.op.history._verified(x.source.read_authority())["state"]
    for item in mod.op.history.list_official_runs(x.source.read_authority()):
        prior_view.append(
            executor.PriorRun(
                item["run_id"], OFFICIAL, item["lifecycle_state"] == "TERMINAL", item["result"], ()
            )
        )

    def admit(**kw):
        assert (
            kw["context"].holder.public_key().public_bytes_raw()
            == a.holder.public_key().public_bytes_raw()
        )
        assert kw["context"].owner == (os.getpid(), threading.get_ident())
        assert kw["action"] == "start"
        native.qualifier.qualify()

    def guard(**kw):
        native.qualifier.qualify()
        assert kw["context"].owner == (os.getpid(), threading.get_ident())
        assert kw["run_id"] is None or kw["run_id"] == kw["context"].proposal["run_id"]

    def prior(**kw):
        snapshot = x.source.read_authority()
        assert (
            mod.op.history._verified(snapshot)["state"]["official_runs"]
            == material["official_runs"]
        )
        if kw.get("common") is not None:
            assert kw["common"] == tuple(prior_view)

    def commit(**kw):
        result = kw["response"].to_dict()["result"]
        state = a.journal.read()
        record = state["latest"][result["reservation"]["reservation_id"]]
        assert record == result["reservation"] and record["state"] == "COMMITTED"
        receipt = mod.op.sql.parse_canonical(
            a.journal.receipt_store.read(record["reservation_receipt_reference"]["path"])
        )
        expected = {k: v for k, v in receipt.items() if k not in ("issued_at", "signature")}
        mod.wire.verify_reservation_receipt(
            mod.op.sql.canonical_bytes(receipt),
            expected_context=mod.wire.ReceiptContext(mod.op.sql.canonical_bytes(expected), a.now()),
            witness_pin=a.journal.floor.pins[0],
        )
        assert receipt["transition"] == "COMMIT_OPEN" and receipt["state"] == "COMMITTED"
        assert receipt["reservation_digest"] == record["reservation_digest"]
        snapshot = x.source.read_authority()
        assert (
            mod.op.history.current_revision(snapshot)["revision"]
            == record["expected_next_revision"]
        )
        # Full B3 replay includes byte/authentication verification in the actual
        # fixture trust store; native GOV was qualified by publisher before CAS.
        assert any(
            r["run_id"] == record["run_id"]
            for r in mod.op.history._verified(snapshot)["state"]["official_runs"]
        )

    bindings = None
    current_guard, common_reader, common_verifier, commit_verifier = (
        guard,
        lambda **kw: tuple(prior_view),
        prior,
        commit,
    )
    if client_proofs:
        bindings = _v2_client_proof_original_bindings(
            store=store,
            channel=channel,
            fixture=fixture,
            source=x.source,
            clock=a.now,
        )
        current_guard, common_reader, common_verifier, commit_verifier = (
            bindings.guard,
            bindings.prior_reader,
            bindings.prior_verifier,
            bindings.verify_commit,
        )
    if guard_factory is not None:
        current_guard = guard_factory(current_guard)
        assert callable(current_guard)
        if bindings is not None:
            bindings.guard = current_guard
    context = executor.ExecutorOperationalContext(
        action="start",
        channel=channel,
        bridge_pin=client_wire.PublicKeyPin(
            a.bridge_pin.key_id, a.bridge_pin.key_epoch, a.bridge_pin.public_key
        ),
        witness_pin=client_wire.PublicKeyPin(
            a.journal.floor.pins[0].key_id,
            a.journal.floor.pins[0].key_epoch,
            a.journal.floor.pins[0].public_key,
        ),
        holder_key=a.holder,
        expected_challenge=fixed,
        request_payload={
            k: v
            for k, v in raw["payload"].items()
            if k not in ("run_identity", "preflight_reference")
        },
        evidence_root=str(store.root),
        host_evidence_root=str(store.root),
        staging_name="application-preflight." + str(uuid.uuid4()) + ".json",
        authority_revision=raw["authority_revision"],
        host_root_binding_sha256=rig.host_policy.root_binding,
        native_admitter=admit,
        current_guard=current_guard,
        common_prior_reader=common_reader,
        common_prior_verifier=common_verifier,
        commit_verifier=commit_verifier,
        terminal_preparer=lambda **kw: None,
        clock=a.now,
    )
    context.prepare_start(
        store,
        probes=probes,
        git_sha=fixed["source_revision"],
        source_root=Path(native.value["source_host_root"]),
        nonce=execution_nonce,
    )
    reference = {
        "path": str(store.root / context._cfg()["staging_name"]),
        "sha256": sha256_bytes(context.preflight),
        "schema_version": report["schema_version"],
    }
    preflight_store = mod.op.durable.PrivateStore(str(store.root), mod.h.identity(store.root))
    # The coordination fixture owns this lease until all consumers have stopped.
    a.stores["w08-preflight-" + str(uuid.uuid4())] = preflight_store
    binding = mod.op.HostArtifactBinding(
        "preflight",
        mod.op.sql.canonical_bytes(reference),
        preflight_store,
    )
    registry_bindings = [b for name, b in x.source.registry.bindings.items() if name != "preflight"]
    x.source.registry = mod.op.HostArtifactRegistry(tuple(registry_bindings + [binding]))
    x.host.deadline = time.monotonic() + original_budget
    broker = mod.op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=native.qualifier,
        signer=a.bridge,
        host_sources=x.source,
        holder_admitter=lambda **kw: a.holder.public_key(),
    )
    return SimpleNamespace(
        context=context,
        client_proof_bindings=bindings,
        store=store,
        probes=probes,
        broker=broker,
        host=x.host,
        fixture=fixture,
        temporary_store=store.root.parent,
        required=required,
        approved_acknowledgments=(
            tuple(p.run_id for p in prior_view if p.decision == "NOT_DEMONSTRATED")
            if acknowledgments is None
            else tuple(acknowledgments)
        ),
    )


def test_v2_actual_executor_start_nine_frames_native_cas_and_exact_four_artifacts(
    v2_native_host, dataset
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    errors = []
    responses = []

    def host():
        try:
            responses.append(rig.broker.serve_start(rig.host))
        except BaseException as error:
            errors.append(error)
            rig.host.close()

    thread = threading.Thread(target=host)
    thread.start()
    try:
        context = rig.context
        result = executor.start_run(
            rig.store,
            git_sha=context.proposal["git_sha"],
            source_root=Path(v2_native_host.native.value["source_host_root"]),
            nonce=context.proposal["execution_nonce"],
            evidence_mode=OFFICIAL,
            timezone="UTC",
            acknowledged_not_demonstrated=[
                p.run_id
                for p in context.prior_reader(context=context)
                if p.decision == "NOT_DEMONSTRATED"
            ],
            probes=rig.probes,
            operational_context=context,
        )
        assert result["run_id"] == context.proposal["run_id"]
        assert context.finished and context.consumed and context.scientific_finished
        assert rig.host.position == context.channel.position == 9
        records = rig.store.records(result["run_id"])
        assert {r["artifact_type"] for r in records} == {
            executor.ART_MANIFEST_INITIAL,
            executor.ART_START,
            executor.ART_PREFLIGHT,
            executor.ART_PLAN,
        }
        state = executor.load_existing_state_readonly(rig.store, result["run_id"])
        assert (
            state.payload(executor.ART_MANIFEST_INITIAL)["started_at"]
            == context.proposal["started_at"]
        )
        assert canonical_json_bytes(state.payload(executor.ART_PREFLIGHT)) == context.preflight
        assert v2_native_host.rig.a.transport.calls.count("RESERVE") == 1
        assert v2_native_host.source.source.publisher.transport.calls.count("B3_CAS") == 1
    finally:
        rig.context.channel.close()
        rig.host.close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors, [type(e).__name__ + ":" + str(e) for e in errors]
    assert len(responses) == 1


@pytest.mark.parametrize("proof", [True, False, {}, SimpleNamespace(verified=True)])
def test_v2_boolean_or_untyped_official_proof_denied_before_any_probe(h, proof):
    h.set_probes(
        preflight=lambda options: pytest.fail("preflight after untyped context"),
        dataset=lambda: pytest.fail("dataset after untyped context"),
    )
    before = sorted(str(p.relative_to(h.store.root.parent)) for p in h.store.root.parent.rglob("*"))
    error = refusal(h.start, evidence_mode=OFFICIAL, operational_context=proof)
    assert error.reasons == ("operational-context-required",) and error.terminal
    assert (
        sorted(str(p.relative_to(h.store.root.parent)) for p in h.store.root.parent.rglob("*"))
        == before
    )


@pytest.mark.parametrize("proof", [True, {}, SimpleNamespace(verified=True)])
def test_v2_local_test_cannot_accept_any_official_context(h, proof):
    h.set_probes(preflight=lambda options: pytest.fail("LOCAL_TEST consumed context"))
    error = refusal(h.start, operational_context=proof)
    assert error.reasons == ("local-test-official-context",)
    assert not (h.store.root / "runs").exists()


def test_v2_cli_selector_requires_independent_bootstrap_and_never_echoes_input(cli, h, capsys):
    with pytest.raises(CommandError) as error:
        cli(
            "start",
            "--expected-git-sha",
            SHA,
            "--source-root",
            "/nonexistent",
            "--nonce",
            "a" * 64,
            "--evidence-mode",
            OFFICIAL,
            "--confirm-new-run",
            "--operational-context-stdin",
        )
    assert error.value.returncode == 2
    assert "operational-context-bootstrap-required" in capsys.readouterr().err
    assert not (h.store.root / "runs").exists()


def test_v2_cli_local_selector_denied_before_bootstrap(cli, h, monkeypatch):
    monkeypatch.setattr(
        s11_ex10.Command, "operational_context", lambda *args: pytest.fail("LOCAL_TEST bootstrap")
    )
    with pytest.raises(CommandError) as error:
        cli(*start_args("nonce-selector-01"), "--operational-context-stdin")
    assert error.value.returncode == 2
    assert not (h.store.root / "runs").exists()


def v2_run_native_host(rig, operation):
    """Finish or fail the actual owned host exchange once; never restart/retry."""
    errors = []
    rig.host_errors = errors
    responses = []

    def host():
        try:
            responses.append(rig.broker.serve_start(rig.host))
        except BaseException as error:
            errors.append(error)
            rig.host.close()

    thread = threading.Thread(target=host)
    thread.start()
    try:
        return operation(), errors, responses
    finally:
        rig.context.channel.close()
        # Deliver genuine client EOF before closing the host's retained FDs.
        thread.join(timeout=5)
        if thread.is_alive():
            rig.host.close()
            thread.join(timeout=5)
        else:
            rig.host.close()
        assert not thread.is_alive()


def v2_start_call(rig):
    context = rig.context
    return executor.start_run(
        rig.store,
        git_sha=context.proposal["git_sha"],
        source_root=Path(rig.fixture.native.value["source_host_root"]),
        nonce=context.proposal["execution_nonce"],
        evidence_mode=OFFICIAL,
        timezone="UTC",
        acknowledged_not_demonstrated=rig.approved_acknowledgments,
        probes=rig.probes,
        operational_context=context,
    )


def test_v2_current_process_guard_never_accepts_boolean(v2_native_host, dataset):
    rig = v2_native_start_setup(v2_native_host, dataset)
    rig.context.admit = lambda **kw: True
    try:
        with pytest.raises(executor.ExecutorBridgeError, match="PROCESS_ADMISSION_REQUIRED"):
            v2_start_call(rig)
        assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 0
        assert not (rig.store.root / "runs").exists()
    finally:
        rig.context.channel.close()


def test_v2_local_run_omitted_from_common_view_blocks_before_delivery(v2_native_host, dataset):
    rig = v2_native_start_setup(v2_native_host, dataset)
    local = executor.start_run(
        rig.store,
        git_sha=rig.context.proposal["git_sha"],
        source_root=Path(rig.fixture.native.value["source_host_root"]),
        nonce="local-omitted-001",
        evidence_mode=LOCAL,
        timezone="UTC",
        probes=rig.probes,
    )
    before = run_files(rig.store, local["run_id"])

    def operation():
        with pytest.raises(executor.Ex10Refusal) as refusal:
            v2_start_call(rig)
        assert refusal.value.reasons == ("active-run:" + local["run_id"],)

    v2_run_native_host(rig, operation)
    assert run_files(rig.store, local["run_id"]) == before
    assert not list(rig.store.root.glob("delivery-claim.*.json"))
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0


def test_v2_pf_can_expire_after_grant_without_refresh_or_run_write(
    v2_native_host, dataset, monkeypatch
):
    rig = v2_native_start_setup(v2_native_host, dataset, preflight_age=880)
    original = rig.context.consume_delivery
    calls = []

    def stale(store):
        checked = executor.wire._time(json.loads(rig.context.preflight)["checked_at"])
        rig.context.clock = lambda: checked + timedelta(seconds=901)
        calls.append("after-valid-reserve")
        return original(store)

    monkeypatch.setattr(rig.context, "consume_delivery", stale)

    def operation():
        with pytest.raises(executor.ExecutorBridgeError, match="PREFLIGHT_EXPIRED_OR_CHANGED"):
            v2_start_call(rig)

    v2_run_native_host(rig, operation)
    assert calls == ["after-valid-reserve"]
    assert not (rig.store.root / "runs").exists()
    assert not list(rig.store.root.glob("delivery-claim.*.json"))
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0


def test_v2_lost_reserve_response_is_uncertain_and_never_creates_run(
    v2_native_host, dataset, monkeypatch
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    frame = rig.broker._frame

    def close_response(channel, kind, body):
        if kind == "RPC_RESPONSE" and channel.position == 4:
            channel.close()
            return
        return frame(channel, kind, body)

    monkeypatch.setattr(rig.broker, "_frame", close_response)

    def operation():
        with pytest.raises(executor.ExecutorBridgeError, match="UNCERTAIN_PUBLICATION"):
            v2_start_call(rig)

    v2_run_native_host(rig, operation)
    assert rig.context.channel.dispatched
    assert not (rig.store.root / "runs").exists()
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0


def test_v2_commit_requires_independent_witness_and_full_authority_qualification(
    v2_native_host, dataset
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    rig.context.verify_commit = lambda **kw: True

    def operation():
        with pytest.raises(executor.ExecutorBridgeError, match="UNCERTAIN_PUBLICATION"):
            v2_start_call(rig)

    v2_run_native_host(rig, operation)
    assert rig.context.consumed and not rig.context.finished
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 1
    assert len(list((rig.store.root / "runs").iterdir())) == 1
    assert len(rig.store.records(rig.context.proposal["run_id"])) == 4


def test_v2_terminal_cannot_publish_before_seal_even_with_typed_start_context(
    v2_native_host, dataset
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    publishes = []
    rig.context.prepare_terminal = lambda **kw: publishes.append(kw)
    try:
        with pytest.raises(executor.ExecutorBridgeError, match="TERMINAL_PUBLICATION_DENIED"):
            rig.context.prepare_sealed_terminal(
                rig.store,
                run_id=rig.context.proposal["run_id"],
                result={"run_id": rig.context.proposal["run_id"]},
            )
        assert not publishes
        assert not (rig.store.root / "runs").exists()
    finally:
        rig.context.channel.close()


def test_v2_running_sources_cover_shared_imports_and_preserve_old_local_storage():
    from apps.experiments_app import ex10_preflight as pf

    root = Path(executor.__file__).resolve().parents[3]
    required = {
        "ex10_bridge_protocol.py",
        "ex10_open_evidence.py",
        "ex10_runtime_profile.py",
        "ex10_session_v2.py",
        "ex10_deployment_envelope.py",
        "canonical.py",
        "constants.py",
        "manifest.py",
        "evidence.py",
        "custody_policy.py",
        "redaction.py",
        "runtime.py",
        "custody.py",
        "isolation.py",
    }
    inventory = set(pf.RUNNING_SOURCES)
    assert {"backend/apps/experiments_app/" + name for name in required} <= inventory
    assert "backend/apps/experiments_app/local_storage.py" in inventory
    assert not any(
        "ex10_" + name + ".py" in " ".join(inventory)
        for name in (
            "operational_context",
            "authority_sqlite",
            "authority_backend",
            "authority_witness",
            "witness_service",
            "start_coordination",
            "history_authority",
            "governance_certificate",
        )
    )
    for path in inventory:
        assert (root / path).is_file()


def v2_native_single_setup(
    started,
    *,
    action,
    client_proofs=False,
    guard_factory=None,
    terminal_preparer=None,
):
    """Fresh actual pipes/context and native full GOV replay; no second PF/START."""
    import uuid

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from apps.experiments_app import ex10_bridge_protocol as client_wire

    fixture, mod = started.fixture, started.fixture.module
    old = fixture.source.source
    a, native = fixture.rig.a, fixture.native
    snapshot = old.read_authority()
    head = mod.op.history.current_revision(snapshot)
    historical = dict(old.histories(snapshot=snapshot, journal=a.journal).contexts)
    for reference, qualifier in old.catalog.items():
        assert reference not in historical or historical[reference] is qualifier
        historical[reference] = qualifier
    floor = old.publisher.floor
    path = floor._path(head["revision"])
    receipt_raw = floor.store.read(path)
    current_binding = mod.op.HostArtifactBinding(
        "base_receipt",
        mod.op.sql.canonical_bytes(
            {
                "path": path,
                "sha256": sha256_bytes(receipt_raw),
                "schema_version": mod.op.sql.parse_canonical(receipt_raw)["witness_schema_version"],
            }
        ),
        floor.store,
    )
    bindings = tuple(
        binding for name, binding in old.registry.bindings.items() if name != "base_receipt"
    ) + (current_binding,)
    source = mod.op.NativeHostSources(
        session_qualifier=old.sessions,
        registry=mod.op.HostArtifactRegistry(bindings),
        policy=old.policy,
        authority_reader=old.reader,
        historical_contexts=lambda **kw: mod.op.NativeGovernanceHistory(tuple(historical.items())),
        artifact_store=old.artifacts,
        certificate_store=old.certificates,
        publisher=old.publisher,
        executor_fingerprint=old.fingerprint,
        signer=old.signer,
        bridge_pin=old.pin,
    )
    traces = []
    approve_single = source.approve_single

    def traced_single(**kw):
        import traceback

        try:
            return approve_single(**kw)
        except Exception as error:
            traces.append(
                {
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "locations": [
                        (frame.name, frame.lineno)
                        for frame in traceback.extract_tb(error.__traceback__)
                    ],
                }
            )
            raise

    source.approve_single = traced_single
    source.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
    a.journal.sources = source
    snapshot = source.read_authority()
    revision = mod.op.history.current_revision(snapshot)["revision"]
    record = a.journal.read()["latest"][started.context.reservation["reservation_id"]]
    exchange = str(uuid.uuid4())
    hr, ew = os.pipe()
    er, hw = os.pipe()
    original_budget = 120 if client_proofs else 30
    host = mod.op.PipeFrameChannel(
        read_fd=hr, write_fd=hw, exchange="SINGLE_RPC", exchange_id=exchange, budget=original_budget
    )
    client = executor.ExecutorBridgeChannel(
        read_fd=er, write_fd=ew, exchange="SINGLE_RPC", exchange_id=exchange, budget=original_budget
    )
    for fd in (hr, hw, er, ew):
        os.close(fd)
    holder = Ed25519PrivateKey.generate()
    fixed = {**started.context._cfg()["expected_challenge"], "exchange_id": exchange}

    def admit(**kw):
        assert kw["action"] == action
        assert (
            kw["context"].holder.public_key().public_bytes_raw()
            == holder.public_key().public_bytes_raw()
        )
        native.qualifier.qualify()

    def guard(**kw):
        native.qualifier.qualify()
        actual = a.journal.read()["latest"][record["reservation_id"]]
        assert actual == record and actual["state"] == "COMMITTED"
        head = mod.op.history.current_revision(source.read_authority())
        assert head["revision"] == revision
        assert kw["run_id"] is None or kw["run_id"] == record["run_id"]

    def authority(**kw):
        accepted = source.read_authority()
        assert mod.op.history.current_revision(accepted)["revision"] == revision
        assert kw["reserve_response"].to_dict()["result"]["reservation"] == record

    bindings = None
    current_guard, common_verifier = guard, authority
    if client_proofs:
        bindings = _v2_client_proof_original_bindings(
            store=started.store,
            channel=client,
            fixture=fixture,
            source=source,
            clock=a.now,
        )
        current_guard, common_verifier = bindings.guard, bindings.prior_verifier
    if guard_factory is not None:
        current_guard = guard_factory(current_guard)
        assert callable(current_guard)
        if bindings is not None:
            bindings.guard = current_guard
    if terminal_preparer is None:

        def terminal_preparer(**kw):
            pytest.fail("unexpected terminal")

    context = executor.ExecutorOperationalContext(
        action=action,
        channel=client,
        bridge_pin=client_wire.PublicKeyPin(
            a.bridge_pin.key_id, a.bridge_pin.key_epoch, a.bridge_pin.public_key
        ),
        witness_pin=client_wire.PublicKeyPin(
            a.journal.floor.pins[0].key_id,
            a.journal.floor.pins[0].key_epoch,
            a.journal.floor.pins[0].public_key,
        ),
        holder_key=holder,
        expected_challenge=fixed,
        request_payload={
            "reservation_id": record["reservation_id"],
            "original_request_id": record["request_id"],
        },
        evidence_root=str(started.store.root),
        host_evidence_root=str(started.store.root),
        staging_name=None,
        authority_revision=revision,
        host_root_binding_sha256=old.policy.root_binding,
        expected_reservation=record,
        native_admitter=admit,
        current_guard=current_guard,
        common_prior_reader=lambda **kw: (),
        common_prior_verifier=common_verifier,
        commit_verifier=lambda **kw: pytest.fail("nonSTART commit"),
        terminal_preparer=terminal_preparer,
        clock=a.now,
    )
    broker = mod.op.NativeBridgeBroker(
        journal=a.journal,
        session_qualifier=native.qualifier,
        signer=a.bridge,
        host_sources=source,
        holder_admitter=lambda **kw: holder.public_key(),
    )
    return SimpleNamespace(
        context=context,
        client_proof_bindings=bindings,
        host=host,
        broker=broker,
        store=started.store,
        fixture=fixture,
        traces=traces,
    )


def v2_run_single_host(rig, operation):
    errors = []
    responses = []

    def host():
        try:
            responses.append(rig.broker.serve_single(rig.host))
        except BaseException as error:
            import traceback

            errors.append(error)
            rig.traces.append(
                {
                    "host_type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "locations": [
                        (f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)
                    ],
                }
            )
            rig.host.close()

    thread = threading.Thread(target=host)
    thread.start()
    try:
        result = operation()
    except Exception as error:
        error.add_note("Synthetic host provider diagnostic (no values): " + repr(rig.traces))
        raise
    finally:
        rig.context.channel.close()
        thread.join(timeout=5)
        if thread.is_alive():
            rig.host.close()
            thread.join(timeout=5)
        else:
            rig.host.close()
        assert not thread.is_alive()
    assert not errors, [type(e).__name__ + ":" + str(e) for e in errors]
    return result, responses


def test_v2_cli_public_result_guard_failure_is_sanitized_after_native_start(
    v2_native_host, dataset, monkeypatch
):
    denied = []

    def make_guard(guard):
        def deny_result(**kw):
            if kw.get("phase") == "PUBLIC_RESULT":
                assert kw["context"].finished and kw["context"].scientific_finished
                denied.append("PUBLIC_RESULT")
                raise OSError("private-result-path-and-secret")
            return guard(**kw)

        return deny_result

    rig = v2_native_start_setup(v2_native_host, dataset, guard_factory=make_guard)
    monkeypatch.setattr(s11_ex10.Command, "operational_context", lambda self, options: rig.context)
    monkeypatch.setattr(s11_ex10.Command, "_store", staticmethod(lambda: rig.store))
    monkeypatch.setattr(s11_ex10.Command, "probes", lambda self: rig.probes)
    output = io.StringIO()

    def operation():
        with pytest.raises(CommandError) as error:
            call_command(
                "s11_ex10",
                "start",
                "--expected-git-sha",
                rig.context.proposal["git_sha"],
                "--source-root",
                v2_native_host.native.value["source_host_root"],
                "--nonce",
                rig.context.proposal["execution_nonce"],
                "--evidence-mode",
                OFFICIAL,
                "--confirm-new-run",
                "--operational-context-stdin",
                *[
                    part
                    for prior in rig.context.prior_reader(context=rig.context)
                    if prior.decision == "NOT_DEMONSTRATED"
                    for part in ("--acknowledge-not-demonstrated", prior.run_id)
                ],
                stdout=output,
            )
        assert error.value.returncode == 3
        assert "private-result" not in str(error.value)

    v2_run_native_host(rig, operation)
    assert not output.getvalue() and denied == ["PUBLIC_RESULT"]
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 1


@pytest.mark.parametrize("stop", ["tokens", "interrupt"])
def test_v2_execute_stop_before_t0_completes_five_frames_and_public_result(
    v2_native_host, dataset, h, stop
):
    started = v2_native_start_setup(v2_native_host, dataset)
    result, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    rig = v2_native_single_setup(started, action="execute")
    h.store = rig.store
    if stop == "tokens":

        def tokens(_):
            raise RuntimeError("private-token-provider-error")
    else:

        def tokens(_):
            raise KeyboardInterrupt

    probes = replace(
        h.probes,
        now=v2_native_host.rig.a.now,
        fingerprint=lambda: started.fixture.rig.sample["context"].executor_fingerprint,
        issue_tokens=tokens,
        preflight=lambda options: pytest.fail("nonSTART refreshed PF"),
    )

    def operation():
        result = executor.execute_run(
            rig.store,
            run_id=started.context.proposal["run_id"],
            git_sha=started.context.proposal["git_sha"],
            probes=probes,
            operational_context=rig.context,
        )
        assert result["phase"] == "STOPPED_PRE_T0"
        assert rig.context.scientific_finished and rig.context.finished
        rig.context.persist_public_result(rig.store, result)
        return result

    actual, replies = v2_run_single_host(rig, operation)
    assert len(replies) == 1 and rig.host.position == rig.context.channel.position == 5
    assert (
        json.loads(
            (
                rig.store.root / ("executor-result." + rig.context.channel.exchange_id + ".json")
            ).read_bytes()
        )
        == actual
    )
    assert h.backend.accepted == 0
    assert v2_native_host.rig.a.transport.calls.count("RESERVE") == 1
    assert v2_native_host.source.source.publisher.transport.calls.count("B3_CAS") == 1


def test_v2_wrong_store_binding_denied_before_any_probe(v2_native_host, dataset, h):
    rig = v2_native_start_setup(v2_native_host, dataset)
    probes = replace(
        rig.probes, dataset=lambda: pytest.fail("wrong bound root reached dataset probe")
    )
    try:
        with pytest.raises(executor.ExecutorBridgeError, match="ROOT_BINDING_INVALID"):
            executor.start_run(
                h.store,
                git_sha=rig.context.proposal["git_sha"],
                source_root=Path(v2_native_host.native.value["source_host_root"]),
                nonce=rig.context.proposal["execution_nonce"],
                evidence_mode=OFFICIAL,
                timezone="UTC",
                probes=probes,
                operational_context=rig.context,
            )
        assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 0
    finally:
        rig.context.channel.close()


@pytest.mark.parametrize("failure", ["signed-uncertain", "wrong-signature", "wrong-request"])
def test_v2_post_reserve_unqualified_response_is_uncertain(
    v2_native_host, dataset, monkeypatch, failure
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    frame = rig.broker._frame
    observed = []
    mod = rig.fixture.module

    def substitute(channel, kind, body):
        if kind == "RPC_RESPONSE" and channel.position == 4:
            changed = json.loads(canonical_json_bytes(body))
            if failure == "signed-uncertain":
                changed.update(
                    status="UNCERTAIN",
                    uncertain=True,
                    error_class="WITNESS_RESULT_UNKNOWN",
                    result=None,
                )
            elif failure == "wrong-signature":
                changed["signature"] = "0" * 128
            else:
                changed["request_id"] = "11111111-1111-4111-8111-111111111111"
            if failure != "wrong-signature":
                changed = mod.wire.parse_response_bytes(
                    mod.builder.signed_response(changed, rig.fixture.rig.a.bridge)
                ).to_dict()
            # It is a genuine response to a DURABLY successful reservation.
            assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
            observed.append(failure)
            return frame(channel, kind, changed)
        return frame(channel, kind, body)

    monkeypatch.setattr(rig.broker, "_frame", substitute)

    def operation():
        with pytest.raises(executor.ExecutorBridgeError, match="UNCERTAIN_PUBLICATION"):
            v2_start_call(rig)

    v2_run_native_host(rig, operation)
    assert observed == [failure]
    assert rig.context.channel.dispatched and not rig.context.consumed
    assert not (rig.store.root / "runs").exists()
    assert not list(rig.store.root.glob("delivery-claim.*.json"))
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1


def v2_actual_client_proofs(rig):
    """Materialize existing real signed B3 proofs, without a Python host verifier."""
    mod = rig.fixture.module
    source = rig.fixture.source.source
    rig.broker._begin(rig.host)
    rig.broker._challenge(rig.host, source.sessions.qualify())
    rig.context._challenge()
    snapshot = source.read_authority()
    material = snapshot.material
    receipts = mod.op.history._receipts(material["state"])
    proof = executor.ExecutorHostProofs(
        root=str(rig.store.root),
        exchange_id=rig.context.channel.exchange_id,
        b3_pins=[rig.fixture.rig.a.f.pin.record],
        genesis_digest=sha256_bytes(canonical_json_bytes(receipts[0])),
        genesis_binding=receipts[0]["genesis"],
        clock=rig.fixture.rig.a.now,
    )
    directory = rig.store.root / ".s11-operational" / proof.exchange_id
    directory.parent.mkdir(mode=0o700)
    directory.mkdir(mode=0o700)
    head = source.publisher.transport.head_b3(rig.context.host_challenge.to_dict()["challenge"])
    for role, raw in (
        ("base-material", snapshot.canonical_material),
        ("base-head", canonical_json_bytes(head)),
    ):
        path = directory / (role + ".json")
        path.write_bytes(raw)
        path.chmod(0o600)
    return proof, snapshot, directory


def test_v2_client_copied_b3_projection_and_common_dto_match_frozen_native(v2_native_host, dataset):
    rig = v2_native_start_setup(v2_native_host, dataset)
    try:
        proof, snapshot, _ = v2_actual_client_proofs(rig)
        state = snapshot.material["state"]
        for row in state["history_revisions"]:
            revision = row["revision"]
            assert proof._projection(
                state, revision
            ) == rig.fixture.module.op.sql.logical_projection(state, revision)
        actual, head = proof.history("base", rig.context)
        assert actual == state
        assert (
            head["revision"] == rig.fixture.module.op.history.current_revision(snapshot)["revision"]
        )
        assert proof.common_prior(context=rig.context) == rig.context.prior_reader(
            context=rig.context
        )
        proof.verify_common(context=rig.context, common=proof.common_prior(context=rig.context))
        assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 0
    finally:
        rig.context.channel.close()
        rig.host.close()


@pytest.mark.parametrize(
    "change",
    [
        "deleted-run",
        "changed-verdict",
        "head-signature",
        "head-challenge",
        "head-expired",
        "wrong-owner-mode",
        "symlink",
        "extra-row-field",
    ],
)
def test_v2_client_actual_signed_history_cannot_be_forged_or_truncated(
    v2_native_host, dataset, change
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    try:
        proof, snapshot, directory = v2_actual_client_proofs(rig)
        state_path = directory / "base-material.json"
        head_path = directory / "base-head.json"
        material = snapshot.material
        head = json.loads(head_path.read_bytes())
        if change == "deleted-run":
            material["state"]["official_runs"] = []
        elif change == "changed-verdict":
            material["state"]["official_runs"][0]["result"] = "PASS"
        elif change == "extra-row-field":
            material["state"]["metadata"][0]["self_approved"] = True
        elif change == "head-signature":
            head["signature"] = "0" * 128
        elif change == "head-challenge":
            head["challenge"] = "0" * 64
        elif change == "head-expired":
            rig.context.clock = lambda: executor.wire._time(head["expires_at"])
        elif change == "wrong-owner-mode":
            state_path.chmod(0o644)
        else:
            retained = directory / "retained-original.json"
            state_path.rename(retained)
            state_path.symlink_to(retained)
        if change in ("deleted-run", "changed-verdict", "extra-row-field"):
            state_path.write_bytes(canonical_json_bytes(material))
        if change in ("head-signature", "head-challenge"):
            head_path.write_bytes(canonical_json_bytes(head))
        with pytest.raises(
            (
                executor.ExecutorBridgeError,
                executor.wire.BridgeProtocolError,
                executor.profile_codec.RuntimeProfileError,
            )
        ):
            proof.history("base", rig.context)
        assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 0
    finally:
        rig.context.channel.close()
        rig.host.close()


def test_v2_client_rejects_unsigned_rows_beyond_signed_revision(v2_native_host, dataset):
    rig = v2_native_start_setup(v2_native_host, dataset)
    try:
        proof, snapshot, directory = v2_actual_client_proofs(rig)
        material = snapshot.material
        old = material["state"]["official_runs"][0]
        future = dict(
            old,
            run_version=old["run_version"] + 1,
            previous_run_version=old["run_version"],
            introduced_revision=max(r["revision"] for r in material["state"]["history_revisions"])
            + 1,
            result="PASS",
            binding=True,
            ack_required=False,
            validity_classification="VALID_BINDING",
        )
        material["state"]["official_runs"].append(future)
        (directory / "base-material.json").write_bytes(canonical_json_bytes(material))
        with pytest.raises(executor.ExecutorBridgeError, match="HOST_HISTORY_INVALID"):
            proof.common_prior(context=rig.context)
        assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 0
    finally:
        rig.context.channel.close()
        rig.host.close()


@pytest.mark.parametrize(
    "field", ["descriptor", "descriptor_reference", "bootstrap_core", "bootstrap_manifest"]
)
def test_v2_client_rejects_unbound_bootstrap(v2_native_host, dataset, field):
    rig = v2_native_start_setup(v2_native_host, dataset)
    try:
        proof, snapshot, directory = v2_actual_client_proofs(rig)
        material = snapshot.material
        material[field] = {}
        (directory / "base-material.json").write_bytes(canonical_json_bytes(material))
        with pytest.raises(executor.ExecutorBridgeError, match="HOST_HISTORY_INVALID"):
            proof.history("base", rig.context)
        assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 0
    finally:
        rig.context.channel.close()
        rig.host.close()


def test_v2_client_revalidates_directory_permissions_after_read(
    v2_native_host, dataset, monkeypatch
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    try:
        proof, _, directory = v2_actual_client_proofs(rig)
        real_read = executor.os.read
        changed = []

        def alter_permissions(fd, count):
            data = real_read(fd, count)
            if data and not changed:
                directory.parent.chmod(0o755)
                changed.append(True)
            return data

        monkeypatch.setattr(executor.os, "read", alter_permissions)
        with pytest.raises(executor.ExecutorBridgeError, match="HOST_PROOF_CHANGED"):
            proof.read("base-material")
        assert changed == [True]
    finally:
        directory.parent.chmod(0o700)
        rig.context.channel.close()
        rig.host.close()


def v2_client_proof_start_setup(
    rig, monkeypatch, *, omit_commit_material=False, receipt_change=None, b3_reference_change=None
):
    """Full real host qualification publishes existing INTERNAL proof outputs."""
    source = rig.fixture.source.source
    a, mod = rig.fixture.rig.a, rig.fixture.module
    bindings = rig.client_proof_bindings
    assert bindings is not None
    proof = bindings.proof
    assert rig.context.guard is bindings.guard
    assert rig.context.prior_reader is bindings.prior_reader
    assert rig.context.prior_verifier is bindings.prior_verifier
    assert rig.context.verify_commit is bindings.verify_commit
    assert rig.context.channel.budget == 120
    rig.proof_errors = bindings.proof_errors
    directory = rig.store.root / ".s11-operational" / proof.exchange_id
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    directory.mkdir(mode=0o700)
    frame = rig.broker._frame
    challenge = []
    copied = []

    def write(role, raw):
        path = directory / (role + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            assert os.write(fd, raw) == len(raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        copied.append(role)

    def publish(channel, kind, body):
        prefix = None
        if kind == "HOST_CHALLENGE":
            challenge.append(body["challenge"])
            prefix = "base"
        elif kind == "RPC_RESPONSE" and channel.position == 8 and body["status"] == "OK":
            prefix = "commit"
        if prefix is not None:
            accepted = source.read_authority()
            if prefix != "commit" or not omit_commit_material:
                write(prefix + "-material", accepted.canonical_material)
            write(
                prefix + "-head",
                canonical_json_bytes(source.publisher.transport.head_b3(challenge[0])),
            )
            if prefix == "commit":
                record = body["result"]["reservation"]
                write(
                    "commit-receipt",
                    a.journal.receipt_store.read(record["reservation_receipt_reference"]["path"]),
                )
                # Actual B4 witness head, challenge-bound, independently signed.
                write("commit-coordination", canonical_json_bytes(a.transport.head(challenge[0])))
                if receipt_change is not None:
                    # Both envelopes remain validly signed with actual synthetic
                    # fixture keys; the independently bound receipt fact is wrong.
                    body = json.loads(canonical_json_bytes(body))
                    receipt_path = directory / "commit-receipt.json"
                    receipt = json.loads(receipt_path.read_bytes())
                    receipt[receipt_change] = (
                        receipt[receipt_change] + 1
                        if receipt_change == "fencing_token"
                        else "0" * 64
                    )
                    message = executor.wire.parse_reservation_receipt_bytes(
                        canonical_json_bytes(receipt)
                    )
                    receipt["signature"] = a.remote.b4_signer.sign(
                        executor.wire.signing_preimage(message)
                    ).hex()
                    raw_receipt = canonical_json_bytes(receipt)
                    receipt_path.write_bytes(raw_receipt)
                    changed_record = body["result"]["reservation"]
                    changed_record["reservation_receipt_reference"]["sha256"] = sha256_bytes(
                        raw_receipt
                    )
                    changed_record["record_digest"] = executor.wire.reservation_record_digest(
                        executor.wire.parse_reservation_bytes(canonical_json_bytes(changed_record))
                    )
                    coordination_path = directory / "commit-coordination.json"
                    coordination = json.loads(coordination_path.read_bytes())
                    coordination["receipt_digest"] = sha256_bytes(raw_receipt)
                    message = executor.wire.parse_coordination_head_bytes(
                        canonical_json_bytes(coordination)
                    )
                    coordination["signature"] = a.remote.b4_signer.sign(
                        executor.wire.signing_preimage(message)
                    ).hex()
                    coordination_path.write_bytes(canonical_json_bytes(coordination))
                    body = mod.wire.parse_response_bytes(
                        mod.builder.signed_response(body, a.bridge)
                    ).to_dict()
                if b3_reference_change is not None:
                    body = json.loads(canonical_json_bytes(body))
                    reference = body["result"]["open_witness"]["receipt_reference"]
                    reference[b3_reference_change] = (
                        "e" * 64 if b3_reference_change == "sha256" else "s11-foreign-b3-receipt-v1"
                    )
                    body["result"]["reservation"]["open_witness_reference"] = json.loads(
                        canonical_json_bytes(body["result"]["open_witness"])
                    )
                    body["witness_head"] = json.loads(
                        canonical_json_bytes(body["result"]["open_witness"])
                    )
                    changed_record = body["result"]["reservation"]
                    changed_record["record_digest"] = executor.wire.reservation_record_digest(
                        executor.wire.parse_reservation_bytes(canonical_json_bytes(changed_record))
                    )
                    body = mod.wire.parse_response_bytes(
                        mod.builder.signed_response(body, a.bridge)
                    ).to_dict()
        return frame(channel, kind, body)

    monkeypatch.setattr(rig.broker, "_frame", publish)
    return proof, copied


@pytest.mark.parametrize("missing", [False, True])
def test_v2_native_nine_frames_use_copied_proofs_and_preserve_postcommit_uncertainty(
    v2_native_host, dataset, monkeypatch, missing
):
    rig = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    _, copied = v2_client_proof_start_setup(rig, monkeypatch, omit_commit_material=missing)

    def operation():
        if missing:
            with pytest.raises(executor.ExecutorBridgeError, match="UNCERTAIN_PUBLICATION"):
                v2_start_call(rig)
        else:
            try:
                result = v2_start_call(rig)
            except executor.ExecutorBridgeError as error:
                error.add_note(json.dumps(rig.proof_errors, sort_keys=True))
                raise
            assert result["run_id"] == rig.context.proposal["run_id"]
            assert rig.context.finished and rig.context.channel.position == 9

    _, errors, responses = v2_run_native_host(rig, operation)
    assert errors == [] and len(responses) == 1
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 1
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert "base-material" in copied and "commit-receipt" in copied
    assert ("commit-material" in copied) is (not missing)
    if missing:
        assert rig.context.channel.dispatched and not rig.context.finished
        state = executor.load_existing_state_readonly(rig.store, rig.context.proposal["run_id"])
        assert state is not None
        assert len(state.records) == 4


def test_v2_client_rejects_detached_root_pin_lineage(v2_native_host, dataset):
    rig = v2_native_start_setup(v2_native_host, dataset)
    try:
        original, snapshot, _ = v2_actual_client_proofs(rig)
        pin = json.loads(original.pins)[0]
        pin["transport_identity_reference"]["path"] += ".substituted"
        proof = executor.ExecutorHostProofs(
            root=original.root,
            exchange_id=original.exchange_id,
            b3_pins=[pin],
            genesis_digest=original.genesis_digest,
            genesis_binding=json.loads(original.genesis_binding),
            clock=original.clock,
        )
        assert snapshot.material["descriptor"]["trust_root_reference"]["sha256"] != sha256_bytes(
            canonical_json_bytes(pin)
        )
        with pytest.raises(executor.ExecutorBridgeError, match="HOST_HISTORY_INVALID"):
            proof.history("base", rig.context)
        assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 0
    finally:
        rig.context.channel.close()
        rig.host.close()


def test_v2_client_pin_constructor_requires_nfc(v2_native_host, dataset):
    rig = v2_native_start_setup(v2_native_host, dataset)
    try:
        original, _, _ = v2_actual_client_proofs(rig)
        pin = json.loads(original.pins)[0]
        pin["approval_reference"]["path"] += "e\u0301"
        with pytest.raises(executor.ExecutorBridgeError, match="HOST_HISTORY_INVALID"):
            executor.ExecutorHostProofs(
                root=original.root,
                exchange_id=original.exchange_id,
                b3_pins=[pin],
                genesis_digest=original.genesis_digest,
                genesis_binding=json.loads(original.genesis_binding),
                clock=original.clock,
            )
    finally:
        rig.context.channel.close()
        rig.host.close()


@pytest.mark.parametrize("field", ["fencing_token", "open_witness_digest"])
def test_v2_client_commit_receipt_binds_independent_facts(
    v2_native_host, dataset, monkeypatch, field
):
    rig = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    _, copied = v2_client_proof_start_setup(rig, monkeypatch, receipt_change=field)

    def operation():
        with pytest.raises(executor.ExecutorBridgeError):
            v2_start_call(rig)

    _, errors, responses = v2_run_native_host(rig, operation)
    assert errors == [] and len(responses) == 1
    assert "commit-receipt" in copied
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 1
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert rig.context.channel.dispatched and not rig.context.finished


def test_v2_reserve_receipt_hash_is_bound_before_any_initial_write(
    v2_native_host, dataset, monkeypatch
):
    rig = v2_native_start_setup(v2_native_host, dataset)
    frame = rig.broker._frame
    observed = []

    def substitute(channel, kind, body):
        if kind == "RPC_RESPONSE" and channel.position == 4 and body["status"] == "OK":
            body = json.loads(canonical_json_bytes(body))
            receipt = body["result"]["reservation_receipt"]
            receipt["sequence"] += 7
            message = executor.wire.parse_reservation_receipt_bytes(canonical_json_bytes(receipt))
            receipt["signature"] = rig.fixture.rig.a.remote.b4_signer.sign(
                executor.wire.signing_preimage(message)
            ).hex()
            assert (
                sha256_bytes(canonical_json_bytes(receipt))
                != body["result"]["reservation"]["reservation_receipt_reference"]["sha256"]
            )
            body = rig.fixture.module.wire.parse_response_bytes(
                rig.fixture.module.builder.signed_response(body, rig.fixture.rig.a.bridge)
            ).to_dict()
            observed.append(True)
        return frame(channel, kind, body)

    monkeypatch.setattr(rig.broker, "_frame", substitute)

    def operation():
        with pytest.raises(executor.ExecutorBridgeError):
            v2_start_call(rig)

    v2_run_native_host(rig, operation)
    assert observed == [True]
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 0
    assert not (rig.store.root / "runs").exists()
    assert not list(rig.store.root.glob("delivery-claim.*.json"))


def v2_client_proof_single_setup(rig, monkeypatch):
    """Actual host qualifies current B3/GOV/floors and copies existing signed outputs."""
    source = rig.broker.sources
    a = rig.fixture.rig.a
    bindings = rig.client_proof_bindings
    assert bindings is not None
    proof = bindings.proof
    assert rig.context.guard is bindings.guard
    assert rig.context.prior_verifier is bindings.prior_verifier
    assert rig.context.channel.budget == 120
    directory = rig.store.root / ".s11-operational" / proof.exchange_id
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    directory.mkdir(mode=0o700)
    frame = rig.broker._frame
    copied = []

    def publish(channel, kind, body):
        if kind == "HOST_CHALLENGE":
            accepted = source.read_authority()
            record = rig.context.reservation
            roles = (
                ("action-material", accepted.canonical_material),
                (
                    "action-head",
                    canonical_json_bytes(source.publisher.transport.head_b3(body["challenge"])),
                ),
                ("action-coordination", canonical_json_bytes(a.transport.head(body["challenge"]))),
                (
                    "action-receipt",
                    a.journal.receipt_store.read(record["reservation_receipt_reference"]["path"]),
                ),
            )
            for role, raw in roles:
                path = directory / (role + ".json")
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    assert os.write(fd, raw) == len(raw)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                copied.append(role)
        return frame(channel, kind, body)

    monkeypatch.setattr(rig.broker, "_frame", publish)
    return proof, copied


def test_v2_native_five_frames_use_copied_current_proofs_before_any_scientific_effect(
    v2_native_host, dataset, monkeypatch, h
):
    started = v2_native_start_setup(v2_native_host, dataset)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    single = v2_native_single_setup(started, action="execute", client_proofs=True)
    _, copied = v2_client_proof_single_setup(single, monkeypatch)
    emitted = []

    def stop(_dataset):
        emitted.append("token-stage")
        raise RuntimeError("synthetic token issuance failed before t0")

    h.store = single.store
    probes = replace(
        h.probes,
        now=started.fixture.rig.a.now,
        fingerprint=lambda: started.fixture.rig.sample["context"].executor_fingerprint,
        issue_tokens=stop,
        preflight=lambda options: pytest.fail("nonSTART refreshed PF"),
    )
    result, responses = v2_run_single_host(
        single,
        lambda: executor.execute_run(
            single.store,
            run_id=single.context.proposal["run_id"],
            git_sha=single.context.proposal["git_sha"],
            probes=probes,
            operational_context=single.context,
        ),
    )
    assert result["phase"] == "STOPPED_PRE_T0" and len(responses) == 1
    assert single.context.channel.position == 5 and single.context.scientific_finished
    assert emitted == ["token-stage"]
    assert h.backend.accepted == 0
    assert set(copied) == {
        "action-material",
        "action-head",
        "action-coordination",
        "action-receipt",
    }
    assert started.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert started.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert started.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 1


def v2_publish_actual_authority_uncertainty(single):
    """Existing B3 INTERNAL primitive, actual SQLite/witness/floor, no scientific traffic."""
    import uuid

    a, mod, source = single.fixture.rig.a, single.fixture.module, single.broker.sources
    before = source.read_authority()
    revision = mod.op.history.current_revision(before)["revision"] + 1
    event = a.f.event(
        "AUTHORITY_UNCERTAIN",
        sequence=len(before.material["state"]["authority_events"]) + 1,
        run=None,
        revision=revision,
    )
    event["occurred_at"] = executor._utc(a.now())
    operation = str(uuid.uuid4())
    candidate_path = a.stores["candidate"].root + "/action-floor-uncertainty.json"
    old_reader = a.sources.intent_reader
    a.sources.intent_reader = lambda path: (
        a.stores["candidate"].read(path) if path == candidate_path else old_reader(path)
    )
    with mod.op.durable.HeldWriter(
        a.stores["lock"], "global.lock", mod.h.identity(a.lock)
    ) as writer:
        adapter = mod.op.durable.AuthorityBackend(
            ledger_store=source.publisher.ledger,
            ledger_name=source.publisher.name,
            ledger_identity=source.publisher.identity,
            candidate_store=source.publisher.candidates,
            floor=source.publisher.floor,
            writer=writer,
            transport=source.publisher.transport,
            signer_pin=source.publisher.pin,
            clock_health=source.publisher.health,
        )
        arguments = a.f.publication_args(adapter, "UNCERTAINTY")
        arguments.update(
            operation_id=operation,
            occurred_at=event["occurred_at"],
            evidence_reference_id=event["evidence_reference_id"],
            candidate_payload_path=candidate_path,
        )
        arguments["capability"].update(operation_id=operation, expected_revision=revision)
        published = mod.op.history._append_uncertain_state(before, None, event, **arguments)
    floor = source.publisher.floor
    path = floor._path(revision)
    raw = floor.store.read(path)
    binding = mod.op.HostArtifactBinding(
        "base_receipt",
        canonical_json_bytes(
            {
                "path": path,
                "sha256": sha256_bytes(raw),
                "schema_version": json.loads(raw)["witness_schema_version"],
            }
        ),
        floor.store,
    )
    source.registry = mod.op.HostArtifactRegistry(
        tuple(b for name, b in source.registry.bindings.items() if name != "base_receipt")
        + (binding,)
    )
    assert published.authority_uncertainty
    return revision


def test_v2_action_rpc_newer_signed_head_rejects_old_still_fresh_proofs(
    v2_native_host, dataset, monkeypatch, h
):
    started = v2_native_start_setup(v2_native_host, dataset)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    single = v2_native_single_setup(started, action="execute", client_proofs=True)
    proof, _ = v2_client_proof_single_setup(single, monkeypatch)
    source = single.broker.sources
    dispatch = source.dispatch_single
    advanced = []
    actual_responses = []
    frame = single.broker._frame

    def advance_before_actual_read(**arguments):
        import traceback

        try:
            advanced.append(v2_publish_actual_authority_uncertainty(single))
            return dispatch(**arguments)
        except Exception as error:
            single.traces.append(
                {
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "locations": [
                        (f.name, f.lineno) for f in traceback.extract_tb(error.__traceback__)
                    ],
                }
            )
            raise

    def observe_response(channel, kind, body):
        if kind == "RPC_RESPONSE":
            actual_responses.append(body)
        return frame(channel, kind, body)

    # W02 READ responses retain request before/after revision. Deliberately
    # inject a bridge-signed stale-revision envelope containing the real newer
    # B3 head, so the backend must enforce the head's independent floor too.
    normal_response = single.broker._normal_response

    def stale_revision_envelope(authenticated, **arguments):
        arguments["after"] = arguments["before"]
        return normal_response(authenticated, **arguments)

    monkeypatch.setattr(single.broker, "_normal_response", stale_revision_envelope)
    monkeypatch.setattr(source, "dispatch_single", advance_before_actual_read)
    monkeypatch.setattr(single.broker, "_frame", observe_response)
    emitted = []

    def stop(_dataset):
        emitted.append("token-stage")
        raise RuntimeError("synthetic stop before t0")

    h.store = single.store
    probes = replace(
        h.probes,
        now=started.fixture.rig.a.now,
        fingerprint=lambda: started.fixture.rig.sample["context"].executor_fingerprint,
        issue_tokens=stop,
        preflight=lambda options: pytest.fail("nonSTART refreshed PF"),
    )
    with pytest.raises(executor.ExecutorBridgeError):
        v2_run_single_host(
            single,
            lambda: executor.execute_run(
                single.store,
                run_id=single.context.proposal["run_id"],
                git_sha=single.context.proposal["git_sha"],
                probes=probes,
                operational_context=single.context,
            ),
        )
    assert advanced == [2] and len(actual_responses) == 1, single.traces
    assert actual_responses[0]["witness_head"]["revision"] == 2
    assert actual_responses[0]["result"]["reservation"] == single.context.reservation
    head, _ = proof.read("action-head")
    assert head["revision"] == 1 and single.context._now() < executor.wire._time(head["expires_at"])
    assert emitted == [] and h.backend.accepted == 0
    assert started.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert started.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert started.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 2


@pytest.mark.parametrize(
    "field",
    ["open_ledger", "open_witness", "receipt_sha", "receipt_schema", "base_ledger", "base_witness"],
)
def test_v2_committed_receipt_rejects_different_b3_checkpoint(
    v2_native_host, dataset, monkeypatch, field
):
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    proof, _ = v2_client_proof_start_setup(started, monkeypatch)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    a = started.fixture.rig.a
    snapshot = started.fixture.source.source.read_authority()
    record = json.loads(canonical_json_bytes(started.context.reservation))
    if field.startswith("open_"):
        record["open_witness_reference"][field[5:] + "_digest"] = "e" * 64
    elif field == "receipt_sha":
        record["open_witness_reference"]["receipt_reference"]["sha256"] = "e" * 64
    elif field == "receipt_schema":
        record["open_witness_reference"]["receipt_reference"]["schema_version"] = (
            "s11-foreign-witness-v1"
        )
    else:
        record["previous_" + field[5:] + "_digest"] = "e" * 64
    record["reservation_digest"] = executor.wire.reservation_digest(
        executor.wire.parse_reservation_bytes(canonical_json_bytes(record))
    )
    receipt, _ = proof.read("commit-receipt")
    receipt.update(
        reservation_digest=record["reservation_digest"],
        base_authority_witness_digest=record["previous_witness_digest"],
        open_witness_digest=record["open_witness_reference"]["witness_digest"],
    )
    message = executor.wire.parse_reservation_receipt_bytes(canonical_json_bytes(receipt))
    receipt["signature"] = a.remote.b4_signer.sign(executor.wire.signing_preimage(message)).hex()
    raw = canonical_json_bytes(receipt)
    record["reservation_receipt_reference"]["sha256"] = sha256_bytes(raw)
    record["record_digest"] = executor.wire.reservation_record_digest(
        executor.wire.parse_reservation_bytes(canonical_json_bytes(record))
    )
    record = executor.wire.qualify_reservation(canonical_json_bytes(record)).to_dict()
    (Path(proof.root) / ".s11-operational" / proof.exchange_id / "commit-receipt.json").write_bytes(
        raw
    )
    with pytest.raises(executor.ExecutorBridgeError, match="OWNERSHIP_REQUIRED"):
        proof._committed_receipt(
            "commit", started.context, state=snapshot.material["state"], record=record
        )
    assert a.transport.calls.count("RESERVE") == 1
    assert a.transport.calls.count("COMMIT_OPEN") == 1
    assert started.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 1


def test_v2_native_stdio_transfer_closes_actual_os_endpoints_before_worker_continues(h):
    import subprocess
    import sys

    output = h.store.root / "native-fd-disposition.json"
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    code = """
import os,json,time,sys,environ
from pathlib import Path
environ.Env.read_env = classmethod(lambda cls,*args,**kwargs: None)
import django
django.setup()
from apps.experiments_app import ex10_executor as e
channel=e.ExecutorBridgeChannel(read_fd=0,write_fd=1,exchange="SINGLE_RPC",exchange_id="11111111-2222-4333-8444-555555555555")
e._native_transfer_stdio(channel)
closed=[]
for fd in (0,1):
    try:os.fstat(fd)
    except OSError:closed.append(fd)
channel.close()
p=Path(sys.argv[1]);p.write_text(json.dumps(closed));p.chmod(0o600)
time.sleep(1)
"""
    with subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(output)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"},
    ) as process:
        import select

        process.stdin.close()
        process.stdin = None
        assert select.select([process.stdout], [], [], 30)[0]
        assert process.stdout.read(1) == b"" and process.poll() is None
        stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0 and stdout == b"" and stderr == b"", {
        "child_exception_class": stderr.decode(errors="replace").splitlines()[-1].split(":")[0]
        if stderr
        else None
    }
    assert json.loads(output.read_text()) == [0, 1]


@pytest.mark.parametrize("field", ["sha256", "schema_version"])
def test_v2_native_nine_commit_checks_exact_b3_receipt_reference(
    v2_native_host, dataset, monkeypatch, field
):
    rig = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    _, copied = v2_client_proof_start_setup(rig, monkeypatch, b3_reference_change=field)

    def operation():
        with pytest.raises(executor.ExecutorBridgeError, match="UNCERTAIN_PUBLICATION"):
            v2_start_call(rig)

    _, errors, responses = v2_run_native_host(rig, operation)
    assert not errors and len(responses) == 1 and "commit-material" in copied
    assert rig.context.commit_dispatched and not rig.context.finished
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 1
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1


@pytest.fixture
def v2_attached_b5_fixture(monkeypatch):
    """Actual frozen B5 qualifier/bytes and real copied source files; no OS admission claim."""
    import importlib.util

    module_path = Path(__file__).with_name("test_ex10_deployment_envelope.py")
    spec = importlib.util.spec_from_file_location("w08_attached_b5_native_fixture", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generator = module.rig.__wrapped__()
    rig = next(generator)
    try:
        rig.clock[0] = datetime.now(UTC)
        initial_pf = json.loads(Path(rig.pf_ref["path"]).read_bytes())
        initial_pf["checked_at"] = module.b5._utc(rig.clock[0])
        initial_pf_raw = canonical_json_bytes(initial_pf)
        Path(rig.pf_ref["path"]).write_bytes(initial_pf_raw)
        rig.pf_ref["sha256"] = sha256_bytes(initial_pf_raw)
        source = Path(executor.__file__).resolve().parents[3]
        vector = {}
        for name in executor.EXECUTOR_SOURCES:
            raw = (source / name).read_bytes()
            path = rig.repo / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o644)
            vector[name] = sha256_bytes(raw)
        options = dict(rig.options)
        options["running_sources"] = {
            name: vector[name] for name in executor.ex10_preflight.RUNNING_SOURCES
        }
        allowed = {
            c["container_id"] for c in rig.containers if c["service"] in ("backend", "worker")
        }

        def reader(*, container_id, relative_path):
            assert container_id in allowed and relative_path in options["running_sources"]
            return (rig.repo / relative_path).read_bytes()

        options["running_source_reader"] = reader
        options["path_context"] = {
            **options["path_context"],
            "running_sources": sorted(set(vector) | set(options["host_operational_sources"])),
        }
        qualifier = module.b5.EnvelopeQualifier(**options)
        envelope = qualifier.build()
        assert envelope.to_dict()["result"] == "PASS", [
            {"check": row["check"], "code": row["detail_code"]}
            for row in envelope.to_dict()["checks"]
            if row["result"] != "PASS"
        ]
        assert qualifier.qualify(envelope._bytes)._bytes == envelope._bytes
        assert envelope.to_dict()["result"] == "PASS"
        yield SimpleNamespace(
            raw=envelope._bytes,
            value=envelope.to_dict(),
            profile=executor.profile_codec.parse_runtime_profile_bytes(canonical_json_bytes(rig.p)),
            header=rig.header,
            cfg={
                "profile_sha256": rig.header["runtime_profile_sha256"],
                "header_sha256": sha256_bytes(
                    executor.session_codec.canonical_session_header_bytes(rig.header)
                ),
                "source_hashes": vector,
            },
            preflight=Path(rig.pf_ref["path"]).read_bytes(),
            now=rig.clock[0],
        )
    finally:
        generator.close()


def test_v2_attached_b5_current_view_is_distinct_from_stable_descriptor(v2_attached_b5_fixture):
    rig = v2_attached_b5_fixture
    assert (
        rig.value["historical_authority_view_reference"]
        != rig.profile.to_dict()["historical_lifecycle_authority_reference"]
    )
    assert (
        executor._native_envelope_bindings(
            rig.raw,
            profile=rig.profile,
            header=rig.header,
            cfg=rig.cfg,
            preflight=rig.preflight,
            now=rig.now,
            fresh_required=True,
        )
        == rig.value
    )


@pytest.mark.parametrize("action", ["drain", "reconcile", "finalize"])
def test_v2_attached_b5_expiry_does_not_require_a_new_start_pf(v2_attached_b5_fixture, action):
    rig = v2_attached_b5_fixture
    before = sha256_bytes(rig.raw), sha256_bytes(rig.preflight)
    value = executor._native_envelope_bindings(
        rig.raw,
        profile=rig.profile,
        header=rig.header,
        cfg={**rig.cfg, "action": action},
        preflight=rig.preflight,
        now=rig.now + timedelta(hours=1),
        fresh_required=False,
    )
    assert value == rig.value and before == (sha256_bytes(rig.raw), sha256_bytes(rig.preflight))


@pytest.mark.parametrize("action", ["start", "execute"])
def test_v2_attached_b5_fresh_start_execute_required(v2_attached_b5_fixture, action):
    rig = v2_attached_b5_fixture
    with pytest.raises(executor.ExecutorBridgeError, match="DEPLOYMENT_REQUIRED"):
        executor._native_envelope_bindings(
            rig.raw,
            profile=rig.profile,
            header=rig.header,
            cfg={**rig.cfg, "action": action},
            preflight=rig.preflight,
            now=rig.now + timedelta(hours=1),
            fresh_required=True,
        )


@pytest.mark.parametrize("fresh", [True, False])
def test_v2_attached_b5_future_denied_in_every_phase(v2_attached_b5_fixture, fresh):
    rig = v2_attached_b5_fixture
    with pytest.raises(executor.ExecutorBridgeError, match="DEPLOYMENT_REQUIRED"):
        executor._native_envelope_bindings(
            rig.raw,
            profile=rig.profile,
            header=rig.header,
            cfg=rig.cfg,
            preflight=rig.preflight,
            now=rig.now - timedelta(seconds=1),
            fresh_required=fresh,
        )


@pytest.mark.parametrize("change", ["source", "preflight", "header"])
def test_v2_attached_b5_poststart_still_binds_immutable_bytes(v2_attached_b5_fixture, change):
    rig = v2_attached_b5_fixture
    cfg, preflight = dict(rig.cfg), rig.preflight
    if change == "source":
        cfg["source_hashes"] = {
            **cfg["source_hashes"],
            executor.ex10_preflight.RUNNING_SOURCES[0]: "f" * 64,
        }
    elif change == "header":
        cfg["header_sha256"] = "f" * 64
    else:
        preflight += b" "
    with pytest.raises(executor.ExecutorBridgeError, match="DEPLOYMENT_REQUIRED|PREFLIGHT_CHANGED"):
        executor._native_envelope_bindings(
            rig.raw,
            profile=rig.profile,
            header=rig.header,
            cfg=cfg,
            preflight=preflight,
            now=rig.now + timedelta(hours=1),
            fresh_required=False,
        )


@pytest.mark.parametrize(
    "change", [None, "container", "initial", "envelope", "source", "profile", "header"]
)
def test_v2_native_process_commitment_binds_independent_inputs(v2_attached_b5_fixture, change):
    """Observer preimage unit test; does not claim OS or challenge admission."""
    rig = v2_attached_b5_fixture
    cfg, initial, raw, cid = dict(rig.cfg), b"initial-public-bootstrap\n", rig.raw, "a" * 64
    base = executor._native_process_identity(
        cfg=cfg, profile=rig.profile, container_id=cid, initial=initial, envelope=raw
    )
    if change == "container":
        cid = "b" * 64
    elif change == "initial":
        initial += b" "
    elif change == "envelope":
        raw += b" "
    elif change == "source":
        cfg["source_hashes"] = {**cfg["source_hashes"], next(iter(cfg["source_hashes"])): "e" * 64}
    elif change == "profile":
        cfg["profile_sha256"] = "e" * 64
    elif change == "header":
        cfg["header_sha256"] = "e" * 64
    value = executor._native_process_identity(
        cfg=cfg, profile=rig.profile, container_id=cid, initial=initial, envelope=raw
    )
    p = rig.profile.to_dict()
    independent = sha256_bytes(
        canonical_json_bytes(
            {
                "container_id": cid,
                "runtime_profile_sha256": cfg["profile_sha256"],
                "session_header_sha256": cfg["header_sha256"],
                "project": p["project"],
                "source_revision": p["source_revision"],
                "source_hashes": cfg["source_hashes"],
                "native_bootstrap_sha256": sha256_bytes(initial),
            }
        )
    )
    assert value == {
        "container_id": cid,
        "identity_digest": independent,
        "deployment_verification_sha256": sha256_bytes(
            canonical_json_bytes(
                {
                    "container_identity_digest": independent,
                    "operational_envelope_sha256": sha256_bytes(raw),
                }
            )
        ),
    }
    if change is not None:
        assert value["deployment_verification_sha256"] != base["deployment_verification_sha256"]
        assert (value["identity_digest"] == base["identity_digest"]) is (change == "envelope")


@pytest.mark.parametrize(
    "forbidden", [None, "operational_envelope_reference", "run_identity", "preflight_reference"]
)
def test_v2_native_start_initial_payload_has_no_future_artifact_cycle(
    v2_attached_b5_fixture, forbidden
):
    rig = v2_attached_b5_fixture
    known = {
        name: {"synthetic-public-input": name}
        for name in (
            "acknowledgments",
            "deployment_verification_reference",
            "descriptor_reference",
            "expected_head",
            "profile_reference",
            "session_reference",
        )
    }
    cfg = {
        **rig.cfg,
        "action": "start",
        "exchange_id": "11111111-2222-4333-8444-555555555555",
        "request_payload": known,
    }
    initial = canonical_json_bytes(cfg)
    if forbidden is not None:
        cfg["request_payload"] = {**known, forbidden: {"sha256": "f" * 64}}
        with pytest.raises(executor.ExecutorBridgeError, match="NATIVE_BOOTSTRAP_REQUIRED"):
            executor._native_prepared_payload(cfg=cfg, profile=rig.profile, envelope=rig.raw)
    else:
        result = executor._native_prepared_payload(cfg=cfg, profile=rig.profile, envelope=rig.raw)
        assert result["operational_envelope_reference"] == {
            "path": rig.profile.to_dict()["host_roots"]["evidence"]["path"]
            + "/.s11-operational/"
            + cfg["exchange_id"]
            + "/envelope.json",
            "sha256": sha256_bytes(rig.raw),
            "schema_version": executor.deployment_envelope.SCHEMA_VERSION,
        }
        assert canonical_json_bytes(cfg) == initial and result.keys() == known.keys() | {
            "operational_envelope_reference"
        }


def v2_authorized_current_fixture(v2_native_host, dataset, monkeypatch):
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    single = v2_native_single_setup(started, action="execute", client_proofs=True)
    proof, _ = v2_client_proof_single_setup(single, monkeypatch)

    def authorize():
        single.context.authorize_step(
            single.store,
            run_id=single.context.proposal["run_id"],
            action="execute",
            git_sha=single.context.proposal["git_sha"],
            phase="PREPARED",
        )

    _, responses = v2_run_single_host(single, authorize)
    assert len(responses) == 1 and single.context.finished and single.context.step_authorized
    return single, proof


def test_v2_current_head_cannot_expire_during_later_qualification(
    v2_native_host, dataset, monkeypatch
):
    single, proof = v2_authorized_current_fixture(v2_native_host, dataset, monkeypatch)
    now = [single.context._now()]
    single.context.clock = lambda: now[0]
    coordination = proof.coordination

    def delayed(*args, **kw):
        result = coordination(*args, **kw)
        now[0] += timedelta(seconds=241)
        return result

    monkeypatch.setattr(proof, "coordination", delayed)
    with pytest.raises(executor.ExecutorBridgeError, match="HOST_HISTORY_STALE|OWNERSHIP_REQUIRED"):
        proof.current(
            context=single.context,
            action="execute",
            run_id=single.context.proposal["run_id"],
            phase="PREPARED",
        )
    assert not single.context.scientific_finished


def test_v2_native_b5_rechecked_after_current_proof_before_barrier_latch(
    v2_native_host, dataset, monkeypatch, v2_attached_b5_fixture
):
    """Isolated timing boundary with real typed action/proof; no OS/BARRIER qualification claim."""
    single, proof = v2_authorized_current_fixture(v2_native_host, dataset, monkeypatch)
    rig = v2_attached_b5_fixture
    now = [rig.now]
    calls = []

    def read_and_advance(**kw):
        # Genuine current proof, without synthesizing an ARMED/emission claim.
        proof.current(**{**kw, "phase": "PREPARED"})
        calls.append(kw["phase"])
        now[0] += timedelta(seconds=901)

    def envelope(raw):
        return executor._native_envelope_bindings(
            raw,
            profile=rig.profile,
            header=rig.header,
            cfg=rig.cfg,
            preflight=rig.preflight,
            now=now[0],
            fresh_required=True,
        )

    # This existing test isolates the final expiry ordering, not OS/preparation.
    monkeypatch.setattr(executor, "_native_wait_emission", lambda **kw: lambda: None)
    apparatus = SimpleNamespace(
        cfg={"action": "execute", "exchange_id": single.context.channel.exchange_id},
        preparation_deadline=time.monotonic() + 900,
        check_os=lambda: None,
        context=single.context,
        proof=SimpleNamespace(current=read_and_advance),
        _envelope=envelope,
        envelope_bytes=rig.raw,
        emission_admitted=False,
    )
    with pytest.raises(executor.ExecutorBridgeError, match="DEPLOYMENT_REQUIRED"):
        executor.ExecutorNativeBootstrap.current(
            apparatus,
            context=single.context,
            action="execute",
            run_id=single.context.proposal["run_id"],
            phase="BARRIER",
        )
    assert calls == ["BARRIER"] and not apparatus.emission_admitted


def test_v2_native_barrier_latch_never_accepts_unqualified_early_return():
    calls = []
    apparatus = SimpleNamespace(
        context=None,
        proof=SimpleNamespace(current=lambda **kw: calls.append(kw)),
        emission_admitted=False,
    )
    with pytest.raises(executor.ExecutorBridgeError, match="OWNERSHIP_REQUIRED"):
        executor.ExecutorNativeBootstrap.current(
            apparatus, context=None, action="execute", run_id=None, phase="BARRIER"
        )
    assert not calls and not apparatus.emission_admitted


@pytest.mark.parametrize("dispatched", [False, True])
def test_v2_relay_both_eof_share_first_io_deadline(monkeypatch, dispatched):
    """Actual private pipes; no delayed second-leg deadline after outer EOF."""
    allfds = []
    channels = []

    def channel(role):
        read_fd, other_write = os.pipe()
        other_read, write_fd = os.pipe()
        allfds.extend((read_fd, other_write, other_read, write_fd))
        value = executor.ExecutorBridgeChannel(
            read_fd=read_fd,
            write_fd=write_fd,
            role=role,
            exchange="SINGLE_RPC",
            exchange_id="11111111-2222-4333-8444-555555555555",
        )
        channels.append(value)
        return value

    outer, inner = channel("EXECUTOR"), channel("HOST")
    now = [1000.0]
    monkeypatch.setattr(executor.time, "monotonic", lambda: now[0])
    try:
        executor._native_share_relay_deadline(outer, inner)
        assert outer.deadline == inner.deadline == 1120.0
        now[0] = 1119.0
        inner._activate()
        if dispatched:
            inner.mark_dispatched()
        outer.close()
        now[0] = 1120.0
        with pytest.raises(
            executor.ExecutorBridgeError,
            match="UNCERTAIN_PUBLICATION" if dispatched else "EXCHANGE_EXPIRED",
        ):
            inner.finish()
    finally:
        for value in channels:
            value.close()
        for fd in allfds:
            os.close(fd)


@pytest.mark.parametrize("action", ["start", "drain"])
def test_v2_native_preparation_waits_for_all_roles_before_frame_budget(monkeypatch, action):
    now = [0.0]
    available = set()
    cfg = {
        "action": action,
        "exchange_id": "11111111-2222-4333-8444-555555555555",
        "staging_name": "application-preflight.11111111-2222-4333-8444-555555555555.json",
    }
    base = "/s11-evidence/.s11-operational/" + cfg["exchange_id"]
    ticks = []

    def sleep(_seconds):
        now[0] += 200
        available.add("/s11-evidence/" + cfg["staging_name"])
        if now[0] >= 600:
            available.add(base + "/pending-session.json")
        if now[0] >= 800:
            available.update((base + "/process.json", base + "/envelope.json"))
        if now[0] >= 1000:
            available.add(base + "/bridge-ready.json")

    monkeypatch.setattr(executor.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(executor.time, "sleep", sleep)
    monkeypatch.setattr(Path, "exists", lambda path: str(path) in available)
    executor._native_wait_prepared(
        cfg=cfg, deadline=1100, guard=lambda: ticks.append(now[0]), alive=lambda: True
    )
    expected = 1000 if action == "start" else 800
    assert now[0] == expected
    assert ticks == [0, 200, 400, 600, 800] + ([1000] if action == "start" else [])
    now[0] = 900
    with pytest.raises(executor.ExecutorBridgeError, match="START_NOT_PREPARED"):
        executor._native_wait_prepared(
            cfg=cfg, deadline=900, guard=lambda: None, alive=lambda: True
        )


def test_v2_native_preparation_never_respawns_a_dead_worker(monkeypatch):
    monkeypatch.setattr(executor.time, "monotonic", lambda: 0)
    monkeypatch.setattr(executor.time, "sleep", lambda seconds: pytest.fail("dead worker waited"))
    with pytest.raises(executor.ExecutorBridgeError, match="START_NOT_PREPARED"):
        executor._native_wait_prepared(
            cfg={"action": "drain", "exchange_id": "11111111-2222-4333-8444-555555555555"},
            deadline=900,
            guard=lambda: None,
            alive=lambda: False,
        )


def test_v2_barrier_current_heads_still_fresh_after_final_b5_read(
    v2_native_host, dataset, monkeypatch, v2_attached_b5_fixture
):
    """Final clock boundary with genuine signed heads; no OS/emission claim."""
    single, proof = v2_authorized_current_fixture(v2_native_host, dataset, monkeypatch)
    rig, now = v2_attached_b5_fixture, [single.context._now()]
    single.context.clock = lambda: now[0]
    current = proof.current

    def qualified(**kw):
        return current(**{**kw, "phase": "PREPARED"})

    monkeypatch.setattr(proof, "current", qualified)

    def envelope(raw):
        result = executor._native_envelope_bindings(
            raw,
            profile=rig.profile,
            header=rig.header,
            cfg=rig.cfg,
            preflight=rig.preflight,
            now=rig.now,
            fresh_required=True,
        )
        now[0] += timedelta(seconds=241)
        return result

    # This existing test isolates the final expiry ordering, not OS/preparation.
    monkeypatch.setattr(executor, "_native_wait_emission", lambda **kw: lambda: None)
    apparatus = SimpleNamespace(
        cfg={"action": "execute", "exchange_id": single.context.channel.exchange_id},
        preparation_deadline=time.monotonic() + 900,
        check_os=lambda: None,
        context=single.context,
        proof=proof,
        _envelope=envelope,
        envelope_bytes=rig.raw,
        emission_admitted=False,
    )
    with pytest.raises(executor.ExecutorBridgeError, match="HOST_HISTORY_STALE"):
        executor.ExecutorNativeBootstrap.current(
            apparatus,
            context=single.context,
            action="execute",
            run_id=single.context.proposal["run_id"],
            phase="BARRIER",
        )
    assert not apparatus.emission_admitted


def test_v2_barrier_b5_and_signed_heads_use_one_final_clock(
    v2_native_host, dataset, monkeypatch, v2_attached_b5_fixture
):
    """Isolated expiry-order unit with genuine heads; no raw B5/OS admission claim."""
    single, proof = v2_authorized_current_fixture(v2_native_host, dataset, monkeypatch)
    rig, now = v2_attached_b5_fixture, [single.context._now()]
    single.context.clock = lambda: now[0]
    current = proof.current
    monkeypatch.setattr(proof, "current", lambda **kw: current(**{**kw, "phase": "PREPARED"}))

    def envelope(raw):
        result = executor._native_envelope_bindings(
            raw,
            profile=rig.profile,
            header=rig.header,
            cfg=rig.cfg,
            preflight=rig.preflight,
            now=rig.now,
            fresh_required=True,
        )
        # Only this temporal unit projects the qualified time window to its
        # final second. Product callers retain exactly the original B5 bytes.
        result = {**result, "expires_at": executor._utc(now[0] + timedelta(seconds=1))}
        now[0] += timedelta(seconds=2)
        return result

    # This existing test isolates the final expiry ordering, not OS/preparation.
    monkeypatch.setattr(executor, "_native_wait_emission", lambda **kw: lambda: None)
    apparatus = SimpleNamespace(
        cfg={"action": "execute", "exchange_id": single.context.channel.exchange_id},
        preparation_deadline=time.monotonic() + 900,
        check_os=lambda: None,
        context=single.context,
        proof=proof,
        _envelope=envelope,
        envelope_bytes=rig.raw,
        emission_admitted=False,
    )
    with pytest.raises(executor.ExecutorBridgeError, match="DEPLOYMENT_REQUIRED"):
        executor.ExecutorNativeBootstrap.current(
            apparatus,
            context=single.context,
            action="execute",
            run_id=single.context.proposal["run_id"],
            phase="BARRIER",
        )
    assert not apparatus.emission_admitted


@pytest.mark.parametrize(
    "change", [None, "rw", "root", "source", "target", "regular", "zero-device"]
)
def test_v2_native_dev_null_mask_uses_actual_device_fd(v2_attached_b5_fixture, monkeypatch, change):
    rig = v2_attached_b5_fixture
    mask = {
        "relative_path": ".env",
        "kind": "dev_null_file",
        "source_path": "/dev/null",
        "read_only": True,
    }
    mounts = {"/s11-full-source/.env": ("/null", frozenset({"ro", "nosuid"}))}
    original = executor.profile_codec._walk
    actual = "/dev/null"
    if change == "rw":
        mounts["/s11-full-source/.env"] = ("/null", frozenset({"rw"}))
    elif change == "root":
        mounts["/s11-full-source/.env"] = ("/other", frozenset({"ro"}))
    elif change == "source":
        mask["source_path"] = "/dev/zero"
    elif change == "target":
        mask["relative_path"] = "alternate"
    elif change == "regular":
        actual = rig.profile.to_dict()["override_path"]
    elif change == "zero-device":
        actual = "/dev/zero"

    def walk(target, kind):
        assert target == "/s11-full-source/.env" and kind == "dev_null"
        return original(actual, kind)

    monkeypatch.setattr(executor.profile_codec, "_walk", walk)
    if change is None:
        assert executor._native_dev_null_mask(mask, mounts) is None
    else:
        with pytest.raises(executor.ExecutorBridgeError, match="ROOT_BINDING_INVALID"):
            executor._native_dev_null_mask(mask, mounts)


def test_v2_finalize_only_prepares_seal_leaves_common_terminal_publication_to_host(
    v2_native_host, dataset, monkeypatch
):
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    single = v2_native_single_setup(started, action="finalize", client_proofs=True)
    v2_client_proof_single_setup(single, monkeypatch)
    source, a = single.broker.sources, single.fixture.rig.a
    revision = single.fixture.module.op.history.current_revision(source.read_authority())[
        "revision"
    ]
    prepared = []

    def prepare(*, context, store, run_id, result):
        assert context is single.context and store is single.store and run_id == result["run_id"]
        state = executor.load_existing_state_readonly(store, run_id)
        assert state is not None and state.finalized
        executor.validate_finalized_state(
            state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
        )
        assert (
            single.fixture.module.op.history.current_revision(source.read_authority())["revision"]
            == revision
        )
        assert (
            a.journal.read()["latest"][context.reservation["reservation_id"]]["state"]
            == "COMMITTED"
        )
        prepared.append(result["decision"])

    single.context.prepare_terminal = prepare
    result, responses = v2_run_single_host(
        single,
        lambda: executor.finalize_run(
            single.store,
            run_id=single.context.proposal["run_id"],
            git_sha=single.context.proposal["git_sha"],
            abandon_reason="synthetic-pre-t0-stop",
            probes=started.probes,
            operational_context=single.context,
        ),
    )
    assert len(responses) == 1 and prepared == ["NOT_DEMONSTRATED"]
    assert (
        executor.phase_of(executor.load_existing_state_readonly(single.store, result["run_id"]))
        == P.ABANDONED_PRE_T0
    )
    assert not result["binding"]
    assert single.context.terminal_prepared and single.context.scientific_finished
    assert not hasattr(single.context, "terminal_published")
    single.context.persist_public_result(single.store, result)
    output = single.store.root / ("executor-result." + single.context.channel.exchange_id + ".json")
    assert json.loads(output.read_bytes()) == result
    assert (
        single.fixture.module.op.history.current_revision(source.read_authority())["revision"]
        == revision
    )
    assert (
        a.journal.read()["latest"][single.context.reservation["reservation_id"]]["state"]
        == "COMMITTED"
    )
    assert a.transport.calls.count("COMMIT_OPEN") == 1 and a.transport.calls.count("RETIRE") == 0


@pytest.mark.parametrize("change", ["run_id", "git_sha"])
def test_v2_single_wrong_run_identity_rejected_before_any_control_frame(
    v2_native_host, dataset, monkeypatch, change
):
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    single = v2_native_single_setup(started, action="execute", client_proofs=True)
    v2_client_proof_single_setup(single, monkeypatch)
    values = {
        "run_id": single.context.proposal["run_id"],
        "git_sha": single.context.proposal["git_sha"],
    }
    values[change] = "s11-11111111-2222-5333-8444-555555555555" if change == "run_id" else "0" * 40
    monkeypatch.setattr(
        single.context, "_challenge", lambda: pytest.fail("wrong identity reached control frame")
    )
    before = run_files(single.store, started.context.proposal["run_id"])
    try:
        with pytest.raises(executor.ExecutorBridgeError, match="RUN_ID_MISMATCH"):
            single.context.authorize_step(
                single.store, action="execute", phase="PREPARED", **values
            )
        assert single.context.channel.position == 0 and not single.context.channel.dispatched
        assert run_files(single.store, started.context.proposal["run_id"]) == before
    finally:
        single.context.channel.close()
        single.host.close()


def test_v2_delivery_claim_survives_loss_of_volatile_consumed_latch(
    v2_native_host, dataset, monkeypatch
):
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    consume, observed = started.context.consume_delivery, []

    def once(store):
        consume(store)
        permit = started.context.permit.to_dict()
        claim = store.root / ("delivery-claim." + permit["permit_nonce"] + ".json")
        raw = claim.read_bytes()
        assert json.loads(raw) == {
            "reservation_id": permit["reservation_id"],
            "fencing_token": permit["fencing_token"],
            "permit_digest": permit["permit_digest"],
            "permit_nonce": permit["permit_nonce"],
            "run_identity": started.context.proposal,
        }
        assert stat.S_IMODE(claim.stat().st_mode) == 0o600
        # Only simulate volatile state loss: the genuine permit and durable claim
        # are retained exactly. No production latch reset or second host RPC.
        started.context.consumed = False
        try:
            with pytest.raises(executor.ExecutorBridgeError, match="WRITE_UNKNOWN"):
                consume(store)
            assert claim.read_bytes() == raw
            assert not (store.root / "runs").exists()
        finally:
            started.context.consumed = True
        observed.append(True)

    monkeypatch.setattr(started.context, "consume_delivery", once)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1 and observed == [True]
    assert started.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert started.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1


def test_v2_verified_common_history_keeps_foreign_nd_when_local_root_is_empty(
    v2_native_host, dataset, monkeypatch
):
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    original, observed = executor.verified_prior_ex10_runs, []

    def complete(store, context, *, local=None):
        assert local == [] and context.common is not None
        source = started.fixture.source.source
        snapshot = source.read_authority()
        runs = started.fixture.module.op.history.list_official_runs(snapshot)
        expected = [executor.PriorRun(r["run_id"], OFFICIAL, True, r["result"], ()) for r in runs]
        assert len(expected) == 1 and expected[0].decision == "NOT_DEMONSTRATED"
        actual = original(store, context, local=local)
        assert actual == expected and actual == list(context.common)
        observed.append(True)
        return actual

    monkeypatch.setattr(executor, "verified_prior_ex10_runs", complete)
    result, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1 and observed == [True]
    state = executor.load_existing_state_readonly(started.store, result["run_id"])
    assert (
        state.payload(executor.ART_START)["prior_ex10_runs"][0]["run_id"]
        == started.approved_acknowledgments[0]
    )


@pytest.mark.parametrize("seal", ["missing", "bad-hmac"])
def test_v2_authorized_finalize_requires_genuine_seal_before_terminal_preparation(
    v2_native_host, dataset, monkeypatch, seal
):
    """Native9+5 authorize finalize; no default True or fake finalized state."""
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1
    if seal == "bad-hmac":
        sealed = v2_native_single_setup(started, action="finalize", client_proofs=True)
        v2_client_proof_single_setup(sealed, monkeypatch)

        def prepare(*, context, store, run_id, result):
            state = executor.load_existing_state_readonly(store, run_id)
            assert context is sealed.context and state is not None and state.finalized
            executor.validate_finalized_state(
                state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
            )

        sealed.context.prepare_terminal = prepare
        result, responses = v2_run_single_host(
            sealed,
            lambda: executor.finalize_run(
                sealed.store,
                run_id=started.context.proposal["run_id"],
                git_sha=started.context.proposal["git_sha"],
                abandon_reason="synthetic-pre-t0-stop",
                probes=started.probes,
                operational_context=sealed.context,
            ),
        )
        assert len(responses) == 1 and result["decision"] == "NOT_DEMONSTRATED"
    single = v2_native_single_setup(started, action="finalize", client_proofs=True)
    proof, _ = v2_client_proof_single_setup(single, monkeypatch)
    run_id, git_sha = started.context.proposal["run_id"], started.context.proposal["git_sha"]
    state = executor.load_existing_state_readonly(single.store, run_id)
    phase = str(executor.phase_of(state))
    _, responses = v2_run_single_host(
        single,
        lambda: single.context.authorize_step(
            single.store, action="finalize", run_id=run_id, git_sha=git_sha, phase=phase
        ),
    )
    assert len(responses) == 1 and single.context.step_authorized and single.context.finished
    assert proof.current_observation is not None
    # The mutation happens only after actual native admission. Keep original
    # immutable opening artifacts and B3/B4 proofs, change this local test seal.
    if seal == "bad-hmac":
        path = state.run_root / ".s11-finalized"
        value = json.loads(path.read_bytes())
        value["signature"] = "0" * 64
        path.write_bytes(canonical_json_bytes(value))
    prepared = []
    single.context.prepare_terminal = lambda **kw: prepared.append(kw)
    before = run_files(single.store, run_id)
    expected = executor.ExecutorBridgeError if seal == "missing" else executor.EvidencePolicyError
    with pytest.raises(expected):
        single.context.prepare_sealed_terminal(
            single.store, run_id=run_id, result={"run_id": run_id}
        )
    assert not prepared and not single.context.terminal_prepared
    assert run_files(single.store, run_id) == before
    assert (
        single.fixture.rig.a.journal.read()["latest"][single.context.reservation["reservation_id"]][
            "state"
        ]
        == "COMMITTED"
    )
    assert single.fixture.rig.a.transport.calls.count("RETIRE") == 0


def test_v2_closed_verified_local_diagnostic_does_not_replace_or_block_common_history(
    v2_empty_native_host, dataset, monkeypatch
):
    started = v2_native_start_setup(v2_empty_native_host, dataset, client_proofs=True)
    local = executor.start_run(
        started.store,
        git_sha=started.context.proposal["git_sha"],
        source_root=Path(started.fixture.native.value["source_host_root"]),
        nonce="local-closed-native-001",
        evidence_mode=LOCAL,
        timezone="UTC",
        probes=started.probes,
    )
    final = executor.finalize_run(
        started.store,
        run_id=local["run_id"],
        git_sha=started.context.proposal["git_sha"],
        abandon_reason="synthetic-pre-t0-stop",
        probes=started.probes,
    )
    assert final["decision"] == "NOT_DEMONSTRATED" and not final["binding"]
    local_before = run_files(started.store, local["run_id"])
    prior = executor.prior_ex10_runs(started.store)
    assert prior == [executor.PriorRun(local["run_id"], LOCAL, True, "NOT_DEMONSTRATED", ())]
    proof, _ = v2_client_proof_start_setup(started, monkeypatch)
    assert started.approved_acknowledgments == ()
    result, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1 and result["phase"] == "PREPARED"
    assert started.context.common == ()
    assert proof.observed_revision == 1  # The qualified COMMIT raises the history floor.
    assert run_files(started.store, local["run_id"]) == local_before
    assert started.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert started.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert not started.context._cfg()["payload"]["acknowledgments"]


def v2_publish_sealed_terminal_and_retire(started, result):
    """Test-only HOST boundary after the synthetic worker returns, with real CAS.

    This component boundary is explicit: it does not attest a Docker worker exit.
    W09 separately proves that OS boundary before publishing a public result.
    """
    import copy
    import uuid

    mod, a = started.fixture.module, started.fixture.rig.a
    source = a.journal.sources
    retire_count = a.transport.calls.count("RETIRE_TERMINAL")
    before = source.read_authority()
    prior_record = a.journal.read()["latest"][started.context.reservation["reservation_id"]]
    assert prior_record["state"] == "COMMITTED"
    assert prior_record["run_id"] == result["run_id"]
    base = mod.op.history.current_revision(before)
    revision = base["revision"] + 1
    operation_id = str(uuid.uuid4())
    candidate_path = source.publisher.candidates.root + "/terminal-" + operation_id + ".json"

    def sealed():
        state = executor.load_existing_state_readonly(started.store, result["run_id"])
        assert state is not None and state.finalized
        executor.validate_finalized_state(
            state.run_root, state.records, custody_hmac_key=started.store.custody_hmac_key
        )
        decision = state.payload(executor.ART_DECISION)
        assert decision["decision"] == result["decision"]
        assert decision["binding"] == result["binding"]
        decision_record = state.record(executor.ART_DECISION)
        final_record = state.record(executor.ART_MANIFEST_FINAL)
        assert decision_record["sha256"] == result["decision_sha256"]
        assert final_record["sha256"] == result["manifest_sha256"]
        path = state.run_root / final_record["relative_path"]
        raw = path.read_bytes()
        assert sha256_bytes(raw) == final_record["sha256"]
        return state, {
            "path": str(path),
            "sha256": sha256_bytes(raw),
            "schema_version": json.loads(raw)["schema_version"],
        }

    state, reference = sealed()
    reference.update(kind="EVIDENCE", introduced_revision=revision)
    reference["reference_id"] = mod.op.sql.digest(
        {key: reference[key] for key in ("path", "sha256", "schema_version", "kind")}
    )
    run = copy.deepcopy(mod.op.history.find_run(before, result["run_id"]))
    assert run["lifecycle_state"] == "OPEN" and run["run_version"] == 0
    run.update(
        run_version=1,
        previous_run_version=0,
        introduced_revision=revision,
        lifecycle_state="TERMINAL",
        phase="FINALIZED",
        result=result["decision"],
        binding=result["binding"],
        ack_required=result["decision"] == "NOT_DEMONSTRATED",
        validity_classification=("NON_BINDING_ND" if not result["binding"] else "VALID_BINDING"),
        finalized_at=executor._utc(a.now()),
        evidence_authority_reference_id=reference["reference_id"],
    )
    event = a.f.event(
        "RUN_TERMINAL",
        sequence=len(before.material["state"]["authority_events"]) + 1,
        run=run,
        revision=revision,
    )
    event.update(occurred_at=run["finalized_at"], evidence_reference_id=reference["reference_id"])
    additions = {
        "artifact_references": [reference],
        "official_runs": [run],
        "authority_events": [event],
    }
    capability = {
        "issuer": "synthetic-test-only",
        "authority_id": base["authority_id"],
        "epoch": base["epoch"],
        "operation_id": operation_id,
        "expected_revision": revision,
        "action": "RUN_TERMINAL",
        "fence": prior_record["fencing_token"],
        "base_revision": base["revision"],
        "base_digest": base["ledger_digest"],
        "reservation_id": prior_record["reservation_id"],
    }
    checks = []
    observed_heads = []

    class SealedTerminalVerifier:
        def verify(
            self, *, authority_id, epoch, operation_id, revision, action, proof, candidate_state
        ):
            assert (authority_id, epoch, operation_id, revision, action, proof) == (
                base["authority_id"],
                base["epoch"],
                capability["operation_id"],
                capability["expected_revision"],
                "RUN_TERMINAL",
                capability,
            )
            _, actual_reference = sealed()
            assert actual_reference == {
                key: reference[key] for key in ("path", "sha256", "schema_version")
            }
            assert a.journal.read()["latest"][prior_record["reservation_id"]] == prior_record
            candidate = mod.op.sql.parse_canonical(candidate_state)
            for table, original in before.material["state"].items():
                if table in (
                    "history_revisions",
                    "witness_checkpoints",
                    "publication_intents",
                    "publication_resolutions",
                ):
                    assert candidate[table][: len(original)] == original
                else:
                    assert candidate[table] == original + additions.get(table, [])
            control_tables = (
                "history_revisions",
                "witness_checkpoints",
                "publication_intents",
                "publication_resolutions",
            )
            suffix = {
                name: candidate[name][len(before.material["state"][name]) :]
                for name in control_tables
            }
            if not checks:
                assert all(rows == [] for rows in suffix.values())
            else:
                assert all(len(rows) == 1 for rows in suffix.values())
                history_row = suffix["history_revisions"][0]
                assert history_row == {
                    "revision": revision,
                    "operation_id": operation_id,
                    "previous_ledger_digest": base["ledger_digest"],
                    "ledger_digest": mod.op.sql.logical_digest(candidate, revision),
                    "recorded_at": run["finalized_at"],
                    "first_event_sequence": event["event_sequence"],
                    "last_event_sequence": event["event_sequence"],
                    "event_count": 1,
                }
                checkpoint = suffix["witness_checkpoints"][0]
                receipt = mod.op.history._receipts(candidate)[-1]
                assert checkpoint == mod.op.history._checkpoint(receipt)
                assert (
                    receipt["operation_id"],
                    receipt["revision"],
                    receipt["ledger_digest"],
                    receipt["previous_witness_digest"],
                    receipt["created_at"],
                    receipt["genesis"],
                ) == (
                    operation_id,
                    revision,
                    history_row["ledger_digest"],
                    base["witness_digest"],
                    run["finalized_at"],
                    None,
                )
                intent = suffix["publication_intents"][0]
                assert intent == {
                    "operation_id": operation_id,
                    "base_revision": base["revision"],
                    "candidate_revision": revision,
                    "base_ledger_digest": base["ledger_digest"],
                    "candidate_ledger_digest": history_row["ledger_digest"],
                    "candidate_payload_path": candidate_path,
                    "candidate_payload_sha256": mod.op.sql.digest(
                        mod.op.sql.logical_projection(candidate, revision)
                    ),
                    "serialization_capability_digest": mod.op.sql.digest(capability),
                    "created_at": run["finalized_at"],
                }
                resolution = suffix["publication_resolutions"][0]
                assert resolution == {
                    "operation_id": operation_id,
                    "disposition": "COMMITTED",
                    "resolved_at": run["finalized_at"],
                    "committed_revision": revision,
                    "receipt_digest": mod.op.sql.digest(receipt),
                    "operator_approval_path": None,
                    "operator_approval_sha256": None,
                    "operator_approval_schema": None,
                    "fresh_witness_observation_digest": mod.op.sql.digest(observed_heads[0]),
                }
                assert len(resolution["fresh_witness_observation_digest"]) == 64
            checks.append("seal-and-exact-candidate")

    with mod.op.durable.HeldWriter(
        a.stores["lock"], "global.lock", mod.h.identity(a.lock)
    ) as writer:
        publisher = source.publisher
        adapter = mod.op.durable.AuthorityBackend(
            ledger_store=publisher.ledger,
            ledger_name=publisher.name,
            ledger_identity=publisher.identity,
            candidate_store=publisher.candidates,
            floor=publisher.floor,
            writer=writer,
            transport=publisher.transport,
            signer_pin=publisher.pin,
            clock_health=publisher.health,
        )
        actual_observe = adapter.observe_head

        def observed(receipt):
            observation = actual_observe(receipt)
            observed_heads.append(observation["head"])
            return observation

        adapter.observe_head = observed
        published = mod.op.history._publication(
            before,
            additions,
            operation_id=operation_id,
            occurred_at=run["finalized_at"],
            evidence_reference_id=reference["reference_id"],
            action="RUN_TERMINAL",
            verifier=SealedTerminalVerifier(),
            capability=capability,
            adapter=adapter,
            signer_identity=publisher.pin,
            candidate_payload_path=candidate_path,
        )
    assert checks == ["seal-and-exact-candidate", "seal-and-exact-candidate"]
    qualified = source.read_authority()
    assert qualified.material["state"] == published.material["state"]
    assert mod.op.history.find_run(qualified, result["run_id"]) == run
    raw_receipt = source.publisher.floor.store.read(source.publisher.floor._path(revision))
    receipt = mod.op.sql.parse_canonical(raw_receipt)
    terminal_reference = {
        "authority_id": base["authority_id"],
        "authority_epoch": base["epoch"],
        "revision": revision,
        "ledger_digest": receipt["ledger_digest"],
        "witness_digest": sha256_bytes(raw_receipt),
        "receipt_reference": {
            "path": source.publisher.floor._path(revision),
            "sha256": sha256_bytes(raw_receipt),
            "schema_version": receipt["witness_schema_version"],
        },
    }

    def qualify_terminal(*, operation_id, record, writer):
        writer.check()
        assert record == prior_record and operation_id == retire_operation
        sealed()
        actual = source.read_authority()
        assert mod.op.history.find_run(actual, record["run_id"]) == run
        assert mod.op.history.current_revision(actual)["revision"] == revision
        return mod.op.coordination.TerminalQualification(
            actual, canonical_json_bytes(terminal_reference), source.publisher.floor
        )

    retire_operation = str(uuid.uuid4())
    source.lifecycle = SimpleNamespace(qualify_terminal=qualify_terminal)
    retired = a.journal.retire_terminal(
        reservation_id=prior_record["reservation_id"], operation_id=retire_operation
    )
    actual = a.journal.read()
    assert actual["active"] is None and actual["witness_active"] is None
    assert retired["terminal_witness_reference"] == terminal_reference
    assert a.transport.calls.count("RETIRE_TERMINAL") == retire_count + 1
    return qualified, retired


def test_v2_sealed_native_terminal_is_published_and_retires_the_synthetic_slot(native_h):
    h = native_h
    result = h.start()
    finished = h.finalize(abandon_reason="synthetic-pre-t0-stop")
    before = run_files(h.store, result["run_id"])
    snapshot, retired = v2_publish_sealed_terminal_and_retire(h.native_started, finished)
    assert run_files(h.store, result["run_id"]) == before
    assert retired["state"] == "COMMITTED"
    assert snapshot.material["state"]["official_runs"][-1]["result"] == "NOT_DEMONSTRATED"
    assert not executor.binding_refusal(
        [executor.PriorRun(result["run_id"], OFFICIAL, True, "NOT_DEMONSTRATED", ())],
        evidence_mode=OFFICIAL,
        acknowledged=(),
    )[1]


def v2_next_native_start_setup(previous, dataset, *, acknowledgments=(), client_proofs=False):
    """New process context/pipes and PF, full common history and explicit test ACKs."""
    import uuid

    fixture, mod = previous.fixture, previous.fixture.module
    a, old = fixture.rig.a, fixture.rig.a.journal.sources
    snapshot = old.read_authority()
    head = mod.op.history.current_revision(snapshot)
    floor = old.publisher.floor
    receipt_path = floor._path(head["revision"])
    raw_receipt = floor.store.read(receipt_path)
    current_binding = mod.op.HostArtifactBinding(
        "base_receipt",
        canonical_json_bytes(
            {
                "path": receipt_path,
                "sha256": sha256_bytes(raw_receipt),
                "schema_version": json.loads(raw_receipt)["witness_schema_version"],
            }
        ),
        floor.store,
    )
    historical = dict(old.histories(snapshot=snapshot, journal=a.journal).contexts)
    for reference, qualifier in old.catalog.items():
        assert reference not in historical or historical[reference] is qualifier
        historical[reference] = qualifier
    source = mod.op.NativeHostSources(
        session_qualifier=old.sessions,
        registry=mod.op.HostArtifactRegistry(
            tuple(
                binding for name, binding in old.registry.bindings.items() if name != "base_receipt"
            )
            + (current_binding,)
        ),
        policy=old.policy,
        authority_reader=old.reader,
        historical_contexts=lambda **kw: mod.op.NativeGovernanceHistory(tuple(historical.items())),
        artifact_store=old.artifacts,
        certificate_store=old.certificates,
        publisher=old.publisher,
        executor_fingerprint=old.fingerprint,
        signer=old.signer,
        bridge_pin=old.pin,
    )
    source.verify_registry(canonical_binding=a.journal.binding, journal=a.journal)
    a.journal.sources = source
    exchange = str(uuid.uuid4())
    hr, ew = os.pipe()
    er, hw = os.pipe()
    host = mod.op.PipeFrameChannel(
        read_fd=hr, write_fd=hw, exchange="START", exchange_id=exchange, budget=120
    )
    client = executor.ExecutorBridgeChannel(
        read_fd=er, write_fd=ew, exchange="START", exchange_id=exchange, budget=120
    )
    for fd in (hr, ew, er, hw):
        os.close(fd)
    source.begin_exchange(channel=host)
    raw = mod.wire.parse_request_bytes(fixture.rig.raw).to_dict()
    raw.update(exchange_id=exchange, authority_revision=head["revision"])
    raw["capability"].update(exchange_id=exchange, expected_revision=head["revision"])
    raw["payload"]["expected_head"] = {
        "authority_id": head["authority_id"],
        "authority_epoch": head["epoch"],
        "revision": head["revision"],
        "ledger_digest": head["ledger_digest"],
        "witness_digest": head["witness_digest"],
        "receipt_reference": json.loads(current_binding.reference),
    }
    raw["payload"]["acknowledgments"] = []
    for run_id in acknowledgments:
        target = mod.op.history.verify_ack_target(snapshot, run_id)
        assert target["recognizable"], "test supplied an invalid ACK target"
        raw["payload"]["acknowledgments"].append(
            {
                "run_id": run_id,
                "authority_revision": target["revision"],
                "terminal_evidence_reference": target["evidence_reference"],
            }
        )
    raw["payload"]["acknowledgments"].sort(key=lambda row: row["run_id"])
    fixture.rig.raw = mod.builder.signed_request(raw, a.bridge, a.holder)
    fixture.source = SimpleNamespace(source=source, host=host, executor=client)
    return v2_native_start_setup(
        fixture,
        dataset,
        execution_nonce=uuid.uuid4().hex * 2,
        acknowledgments=tuple(acknowledgments),
        client_proofs=client_proofs,
    )


def test_v2_second_native_start_after_sealed_nd_requires_exact_common_ack(
    native_h, dataset, monkeypatch
):
    h = native_h
    first = h.start()["run_id"]
    final = h.finalize(abandon_reason="synthetic-pre-t0-stop")
    snapshot, _ = v2_publish_sealed_terminal_and_retire(h.native_started, final)
    assert executor.binding_refusal(
        [executor.PriorRun(first, OFFICIAL, True, "NOT_DEMONSTRATED", ())],
        evidence_mode=OFFICIAL,
        acknowledged=(),
    ) == (["not-demonstrated-unacknowledged:" + first], False)
    before = run_files(h.store, first)
    second = v2_next_native_start_setup(
        h.native_started, dataset, acknowledgments=(first,), client_proofs=True
    )
    v2_client_proof_start_setup(second, monkeypatch)
    result, errors, responses = v2_run_native_host(second, lambda: v2_start_call(second))
    assert not errors and len(responses) == 1 and result["phase"] == "PREPARED"
    assert result["run_id"] != first and run_files(h.store, first) == before
    assert second.context.common == (
        executor.PriorRun(first, OFFICIAL, True, "NOT_DEMONSTRATED", ()),
    )
    assert snapshot.material["state"]["official_runs"][-1]["ack_required"]
    assert h.native_started.fixture.rig.a.transport.calls.count("RESERVE") == 2
    assert h.native_started.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 2


@pytest.fixture
def native_history_h(native_h, dataset, monkeypatch):
    """Original LOCAL API plus Native OFFICIAL actions and genuine terminal/retire.

    Only synthetic, explicitly supplied recognizable ACK IDs enter the native
    request. Invalid caller ACK IDs remain executor arguments for refusal tests.
    The harness never translates a Native refusal into a legacy error.
    """
    h = native_h
    native_steps = {
        name: getattr(h, name) for name in ("execute", "drain", "reconcile", "finalize")
    }
    official_attempts = []

    def start(**kwargs):
        mode = kwargs.pop("evidence_mode", LOCAL)
        if mode == LOCAL:
            return type(h).start(h, evidence_mode=LOCAL, **kwargs)
        assert mode == OFFICIAL and set(kwargs) <= {"acknowledged_not_demonstrated"}
        acknowledged = tuple(kwargs.pop("acknowledged_not_demonstrated", ()))
        if not official_attempts:
            started = h.native_started
            started.approved_acknowledgments = acknowledged
        else:
            old = h.native_started
            mod, a = old.fixture.module, old.fixture.rig.a
            snapshot = a.journal.sources.read_authority()
            known = {row["run_id"] for row in mod.op.history.list_official_runs(snapshot)}
            recognizable = tuple(
                run_id
                for run_id in acknowledged
                if run_id in known
                and mod.op.history.verify_ack_target(snapshot, run_id)["recognizable"]
            )
            started = v2_next_native_start_setup(
                old, dataset, acknowledgments=recognizable, client_proofs=True
            )
            started.approved_acknowledgments = acknowledged
        official_attempts.append(started)
        h.native_started = started
        v2_client_proof_start_setup(started, monkeypatch)
        started.admission_errors = []
        actual_approve = started.broker.sources.approve_reserve

        def admitted(**arguments):
            try:
                return actual_approve(**arguments)
            except started.fixture.module.op.OperationalError as error:
                # Private test diagnostic only. The real broker still sanitizes
                # its public error and retains the original fail-closed path.
                started.admission_errors.append(error.code)
                raise

        started.broker.sources.approve_reserve = admitted
        result, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
        assert not errors and len(responses) == 1
        h.run_ids.append(result["run_id"])
        return result

    def step(name, run_id=None, **kwargs):
        run_id = run_id or h.run_id
        state = executor.load_existing_state_readonly(h.store, run_id)
        assert state is not None
        mode = executor._evidence_mode(state.initial_manifest)
        if mode == LOCAL:
            return getattr(type(h), name)(h, run_id, **kwargs)
        result = native_steps[name](run_id, **kwargs)
        if name == "finalize":
            v2_publish_sealed_terminal_and_retire(h.native_started, result)
        return result

    monkeypatch.setattr(h, "start", start)
    for name in ("execute", "drain", "reconcile", "finalize"):

        def bound(run_id=None, _name=name, **kwargs):
            return step(_name, run_id, **kwargs)

        monkeypatch.setattr(h, name, bound)
    yield h
    for started in official_attempts:
        started.context.channel.close()
        started.host.close()


def native_binding_rejection(h, *, acknowledged=(), keep=()):
    """Keep the legacy semantic oracle and assert genuine Native fail-closed.

    Common OFFICIAL authority denies before capability/RESERVE when binding or
    exact ACK is missing. No invented wire reason or pseudo-authority is used.
    """
    old = h.native_started
    mod, a = old.fixture.module, old.fixture.rig.a
    snapshot = a.journal.sources.read_authority()
    rows = mod.op.history.list_official_runs(snapshot)
    prior = [
        executor.PriorRun(
            row["run_id"], OFFICIAL, row["lifecycle_state"] == "TERMINAL", row["result"], ()
        )
        for row in rows
    ]
    reasons, terminal = executor.binding_refusal(
        prior, evidence_mode=OFFICIAL, acknowledged=acknowledged
    )
    assert reasons
    before = {path: tree(path) for path in keep}
    claims = tuple(h.store.root.glob("delivery-claim.*.json"))
    reserve_count = a.transport.calls.count("RESERVE")
    cas_count = a.journal.sources.publisher.transport.calls.count("B3_CAS")
    commit_count = a.transport.calls.count("COMMIT_OPEN")
    try:
        h.start(evidence_mode=OFFICIAL, acknowledged_not_demonstrated=list(acknowledged))
    except executor.ExecutorBridgeError as error:
        assert error.code == "FRAME_INCOMPLETE"
        assert len(h.native_started.host_errors) == 1
        expected_host_reason = (
            "FIRST_VALID_BLOCKED"
            if terminal or any(not run.finalized for run in prior)
            else "REQUEST_CONTEXT_INVALID"
        )
        assert h.native_started.host_errors[0].code == "HOST_CONTEXT_INVALID"
        assert h.native_started.admission_errors == [expected_host_reason]
        assert a.transport.calls.count("RESERVE") == reserve_count
        assert a.journal.sources.publisher.transport.calls.count("B3_CAS") == cas_count
    except executor.Ex10Refusal as error:
        # Valid common ACKs plus unknown caller IDs reach the legacy domain
        # check after reservation, while retaining zero claim/four-run writes.
        assert set(error.reasons) == set(reasons) and error.terminal == terminal
        assert a.transport.calls.count("COMMIT_OPEN") == commit_count
        assert a.journal.sources.publisher.transport.calls.count("B3_CAS") == cas_count
        journal = a.journal.read()
        record = journal["latest"][h.native_started.context.reservation["reservation_id"]]
        assert record["state"] in ("PENDING", "UNCERTAIN")
        assert journal["active"] is not None
    else:
        pytest.fail("Native admission ignored first-valid/ACK rejection")
    assert tuple(h.store.root.glob("delivery-claim.*.json")) == claims
    assert {path: tree(path) for path in keep} == before
    return reasons, terminal


@pytest.mark.parametrize(
    "mismatch", ["official-omitted", "official-discordant", "local-id-collision"]
)
def test_v2_prior_dto_api_rejects_incomplete_official_copy_or_local_id_collision(
    v2_native_host, dataset, monkeypatch, mismatch
):
    """Typed DTO API negative, on genuine common proof and actual Native reservation.

    The contradictory local DTO is injected at the helper boundary, not claimed
    to be a new sealed run. The independent common authority is never forged.
    """
    rig = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(rig, monkeypatch)
    actual = executor.verified_prior_ex10_runs
    reached = []

    def mismatched(store, context, *, local=None):
        assert context.common is not None and len(context.common) == 1
        assert not local
        common = context.common[0]
        if mismatch == "official-omitted":
            contrary = executor.PriorRun(
                context.proposal["run_id"], OFFICIAL, True, "NOT_DEMONSTRATED", ()
            )
        elif mismatch == "official-discordant":
            contrary = executor.PriorRun(common.run_id, OFFICIAL, True, "FAIL", ())
        else:
            contrary = executor.PriorRun(common.run_id, LOCAL, True, "NOT_DEMONSTRATED", ())
        reached.append(mismatch)
        return actual(store, context, local=[contrary])

    monkeypatch.setattr(executor, "verified_prior_ex10_runs", mismatched)

    def operation():
        with pytest.raises(executor.ExecutorBridgeError, match="LOCAL_COMMON_HISTORY_MISMATCH"):
            v2_start_call(rig)

    v2_run_native_host(rig, operation)
    assert reached == [mismatch]
    assert not (rig.store.root / "runs").exists()
    assert not list(rig.store.root.glob("delivery-claim.*.json"))
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 0


def test_v2_closed_local_invalid_hmac_blocks_native_start_without_repair(
    v2_empty_native_host, dataset, monkeypatch
):
    rig = v2_native_start_setup(v2_empty_native_host, dataset, client_proofs=True)
    local = executor.start_run(
        rig.store,
        git_sha=rig.context.proposal["git_sha"],
        source_root=Path(rig.fixture.native.value["source_host_root"]),
        nonce="local-hmac-native-001",
        evidence_mode=LOCAL,
        timezone="UTC",
        probes=rig.probes,
    )
    executor.finalize_run(
        rig.store,
        run_id=local["run_id"],
        git_sha=rig.context.proposal["git_sha"],
        abandon_reason="synthetic-pre-t0-stop",
        probes=rig.probes,
    )
    run_root = rig.store.root / "runs" / local["run_id"]
    index = run_root / "metadata/artifacts.jsonl"
    index.with_suffix(".jsonl.hmac").write_text("0" * 64 + "\n", encoding="ascii")
    prior = executor.prior_ex10_runs(rig.store)
    assert len(prior) == 1 and prior[0].problems
    before = tree(run_root)
    v2_client_proof_start_setup(rig, monkeypatch)

    def operation():
        with pytest.raises(executor.Ex10Refusal) as error:
            v2_start_call(rig)
        assert error.value.reasons == ("prior-run-unverifiable:" + local["run_id"],)

    v2_run_native_host(rig, operation)
    assert tree(run_root) == before
    assert not list(rig.store.root.glob("delivery-claim.*.json"))
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 0


def test_v2_new_native_start_rejects_obsolete_protocol_identity(native_h, monkeypatch):
    """A new current Native OPEN cannot impersonate the historical science identity."""
    h = native_h
    started = h.native_started
    source = started.broker.sources
    a = started.fixture.rig.a
    before = source.read_authority().canonical_material
    historical = {
        k: v for k, v in ex10.protocol_identity().items() if k != "decision_addendum_a2_commit"
    }
    with monkeypatch.context() as patch:
        patch.setattr(ex10, "protocol_identity", lambda: dict(historical))
        with pytest.raises(executor.ExecutorBridgeError, match="UNCERTAIN_PUBLICATION"):
            h.start(evidence_mode=OFFICIAL)
    assert len(started.host_errors) == 1
    assert isinstance(started.host_errors[0], started.fixture.module.op.OperationalError)
    assert source.read_authority().canonical_material == before
    assert source.publisher.transport.calls.count("B3_CAS") == 0
    assert a.transport.calls.count("COMMIT_OPEN") == 0
    journal = a.journal.read()
    record = journal["latest"][started.context.reservation["reservation_id"]]
    assert record["state"] in ("PENDING", "UNCERTAIN")
    assert journal["active"] is not None
    initial = executor.load_existing_state_readonly(started.store, record["run_id"])
    assert initial is not None
    assert initial.initial_manifest["configuration"]["protocol"] == historical
    assert not initial.finalized


def native_damaged_custody_rejection(h, *, prior, damage, keep):
    """Actual failed Native admission; no invented qualified common history.

    The caller separately checks the original local unverifiable-custody oracle.
    Damage to original OPEN evidence can reject even before new pipes/PF exist.
    Otherwise the unchanged executor or broker still denies the new START.
    """
    started = h.native_started
    mod, a = started.fixture.module, started.fixture.rig.a
    before = {path: tree(path) for path in keep}
    journal = a.journal.read()
    claims = tuple(h.store.root.glob("delivery-claim.*.json"))
    reserve_count = a.transport.calls.count("RESERVE")
    commit_count = a.transport.calls.count("COMMIT_OPEN")
    publisher = a.journal.sources.publisher.transport
    cas_count = publisher.calls.count("B3_CAS")
    try:
        h.start(evidence_mode=OFFICIAL)
    except mod.op.evidence.OpenEvidenceError as error:
        assert damage in {"metadata-removed", "missing-artifact", "symlinked-run"}
        assert error.code == "EVD_PATH_UNSAFE"
    except executor.Ex10Refusal as error:
        assert error.reasons == ("prior-run-unverifiable:" + prior,)
        assert not error.terminal
    except executor.ExecutorBridgeError as error:
        assert error.code == "FRAME_INCOMPLETE"
        assert len(h.native_started.host_errors) == 1
        assert h.native_started.host_errors[0].code == "HOST_CONTEXT_INVALID"
        # Authentic common history may still prove the prior PASS/FAIL binds,
        # even when the later science/index trace cannot be locally verified.
        assert h.native_started.admission_errors == ["FIRST_VALID_BLOCKED"]
    else:
        pytest.fail("Native admission accepted damaged prior custody")
    assert a.journal.read() == journal
    assert a.transport.calls.count("RESERVE") == reserve_count
    assert a.transport.calls.count("COMMIT_OPEN") == commit_count
    assert publisher.calls.count("B3_CAS") == cas_count
    assert tuple(h.store.root.glob("delivery-claim.*.json")) == claims
    assert {path: tree(path) for path in keep} == before


@pytest.fixture
def v2_preemission_rendezvous(v2_native_host, dataset, monkeypatch, h):
    """Real 9/5 frames, B3/B4 and durable ARMED; OS/B5 providers are explicit synthetic inputs."""
    single, proof = v2_authorized_current_fixture(v2_native_host, dataset, monkeypatch)
    a = single.fixture.rig.a
    probes = replace(
        h.probes,
        now=a.now,
        fingerprint=lambda: single.fixture.rig.sample["context"].executor_fingerprint,
    )
    run_id = single.context.proposal["run_id"]
    executor._checkpoint_started(
        single.store,
        run_id=run_id,
        git_sha=single.context.proposal["git_sha"],
        probes=probes,
        dataset=dataset,
        step="execute",
        source="s11_ex10.execute",
        operational_context=single.context,
    )
    executor._arm_emission(
        single.store,
        run_id,
        single.context.proposal["git_sha"],
        probes,
        dataset,
        operational_context=single.context,
    )
    state = executor.load_existing_state_readonly(single.store, run_id)
    assert (
        state is not None
        and state.has(executor.ART_ARMED)
        and executor.phase_of(state) is P.EMITTING
    )
    now = [single.context._now()]
    single.context.clock = lambda: now[0]
    # All synthetic issuers use the same explicit observation clock.
    a.remote.clock = lambda: now[0]
    a.journal.clock = lambda: now[0]
    elapsed = [0.0]
    monkeypatch.setattr(executor.time, "monotonic", lambda: elapsed[0])
    directory = single.store.root / ".s11-operational" / proof.exchange_id
    source = single.broker.sources
    material = source.read_authority().canonical_material
    challenge = single.context.host_challenge.to_dict()["challenge"]
    record = single.context.reservation
    roles = [
        ("emission-material", material),
        ("emission-head", canonical_json_bytes(source.publisher.transport.head_b3(challenge))),
        ("emission-coordination", canonical_json_bytes(a.transport.head(challenge))),
        (
            "emission-receipt",
            a.journal.receipt_store.read(record["reservation_receipt_reference"]["path"]),
        ),
    ]
    published, guards = [], []
    window = {
        "checked_at": executor._utc(now[0] - timedelta(seconds=1)),
        "expires_at": executor._utc(now[0] + timedelta(seconds=900)),
    }

    def envelope(raw):
        assert raw == b"SYNTHETIC-B5-WINDOW-ONLY"
        assert (
            executor.wire._time(window["checked_at"])
            <= now[0]
            < executor.wire._time(window["expires_at"])
        ), "synthetic B5 expired"
        return dict(window)

    def guard():
        single.fixture.native.qualifier.qualify()
        guards.append(elapsed[0])
        try:
            envelope(b"SYNTHETIC-B5-WINDOW-ONLY")
        except AssertionError:
            raise executor.ExecutorBridgeError("DEPLOYMENT_REQUIRED") from None

    def publish(role, raw):
        fd = os.open(
            directory / (role + ".json"),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            assert os.write(fd, raw) == len(raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        published.append(role)

    def sleep(seconds):
        elapsed[0] += seconds
        if len(published) < len(roles):
            publish(*roles[len(published)])

    monkeypatch.setattr(executor.time, "sleep", sleep)
    apparatus = SimpleNamespace(
        context=single.context,
        proof=proof,
        cfg={"action": "execute", "exchange_id": proof.exchange_id},
        preparation_deadline=900.0,
        check_os=guard,
        _envelope=envelope,
        envelope_bytes=b"SYNTHETIC-B5-WINDOW-ONLY",
        emission_admitted=False,
    )
    yield SimpleNamespace(
        single=single,
        proof=proof,
        apparatus=apparatus,
        roles=roles,
        published=published,
        guards=guards,
        elapsed=elapsed,
        now=now,
        window=window,
        publish=publish,
        directory=directory,
        run_id=run_id,
    )


def test_v2_preemission_waits_for_delayed_four_proofs_after_actual_armed(v2_preemission_rendezvous):
    x = v2_preemission_rendezvous
    executor.ExecutorNativeBootstrap.current(
        x.apparatus, context=x.single.context, action="execute", run_id=x.run_id, phase="BARRIER"
    )
    assert x.published == [role for role, _ in x.roles]
    assert x.elapsed[0] >= 0.2 and len(x.guards) >= 4
    assert x.apparatus.emission_admitted
    assert x.single.context.channel.position == 5 and x.single.context.finished
    state = executor.load_existing_state_readonly(x.single.store, x.run_id)
    assert state.has(executor.ART_ARMED) and executor.phase_of(state) is P.EMITTING
    assert (
        x.single.broker.journal.read()["latest"][x.single.context.reservation["reservation_id"]][
            "state"
        ]
        == "COMMITTED"
    )


def test_v2_preemission_partial_publication_keeps_absolute_deadline(
    v2_preemission_rendezvous, monkeypatch
):
    x = v2_preemission_rendezvous
    x.apparatus.preparation_deadline = 0.2

    def partial(seconds):
        x.elapsed[0] += seconds
        if len(x.published) < 3:
            x.publish(*x.roles[len(x.published)])

    monkeypatch.setattr(executor.time, "sleep", partial)
    with pytest.raises(executor.ExecutorBridgeError, match="EMISSION_NOT_PREPARED"):
        executor.ExecutorNativeBootstrap.current(
            x.apparatus,
            context=x.single.context,
            action="execute",
            run_id=x.run_id,
            phase="BARRIER",
        )
    assert not x.apparatus.emission_admitted and len(x.published) == 3
    assert x.elapsed[0] >= 0.2
    assert (
        executor.phase_of(executor.load_existing_state_readonly(x.single.store, x.run_id))
        is P.EMITTING
    )


def test_v2_preemission_lost_process_guard_stops_wait(v2_preemission_rendezvous):
    x = v2_preemission_rendezvous
    genuine = x.apparatus.check_os

    def lost():
        genuine()
        if len(x.guards) >= 2:
            raise executor.ExecutorBridgeError("PROCESS_IDENTITY_CHANGED")

    x.apparatus.check_os = lost
    with pytest.raises(executor.ExecutorBridgeError, match="PROCESS_IDENTITY_CHANGED"):
        executor.ExecutorNativeBootstrap.current(
            x.apparatus,
            context=x.single.context,
            action="execute",
            run_id=x.run_id,
            phase="BARRIER",
        )
    assert not x.apparatus.emission_admitted and len(x.published) < 4


def test_v2_preemission_wait_rejects_b5_expiry_without_refresh(
    v2_preemission_rendezvous, monkeypatch
):
    x = v2_preemission_rendezvous
    x.window["expires_at"] = executor._utc(x.now[0] + timedelta(seconds=0.1))

    def delayed(seconds):
        x.elapsed[0] += seconds
        x.now[0] += timedelta(seconds=0.05)
        if len(x.published) < len(x.roles):
            x.publish(*x.roles[len(x.published)])

    monkeypatch.setattr(executor.time, "sleep", delayed)
    with pytest.raises(executor.ExecutorBridgeError, match="DEPLOYMENT_REQUIRED"):
        executor.ExecutorNativeBootstrap.current(
            x.apparatus,
            context=x.single.context,
            action="execute",
            run_id=x.run_id,
            phase="BARRIER",
        )
    assert not x.apparatus.emission_admitted and len(x.published) < 4


@pytest.mark.parametrize(
    "fault", ["signature", "head-expiry", "symlink", "noncanonical", "replacement"]
)
def test_v2_preemission_wait_never_repairs_invalid_published_proof(
    v2_preemission_rendezvous, monkeypatch, fault
):
    x = v2_preemission_rendezvous
    if fault in {"signature", "head-expiry"}:
        for role, raw in x.roles:
            if fault == "signature" and role == "emission-head":
                value = json.loads(raw)
                value["signature"] = "0" * 128
                raw = canonical_json_bytes(value)
            x.publish(role, raw)
        if fault == "head-expiry":
            x.now[0] += timedelta(seconds=241)
    elif fault == "symlink":
        (x.directory / "emission-head.json").symlink_to("/dev/null")
    elif fault == "noncanonical":
        x.publish("emission-head", b"{} \\n")
    else:
        x.publish(*x.roles[0])

        def replaced(seconds):
            x.elapsed[0] += seconds
            (x.directory / (x.roles[0][0] + ".json")).write_bytes(b"{}\\n")
            for role, raw in x.roles[1:]:
                if role not in x.published:
                    x.publish(role, raw)

        monkeypatch.setattr(executor.time, "sleep", replaced)
    with pytest.raises(executor.ExecutorBridgeError):
        executor.ExecutorNativeBootstrap.current(
            x.apparatus,
            context=x.single.context,
            action="execute",
            run_id=x.run_id,
            phase="BARRIER",
        )
    assert not x.apparatus.emission_admitted
    assert (
        executor.phase_of(executor.load_existing_state_readonly(x.single.store, x.run_id))
        is P.EMITTING
    )


@pytest.mark.parametrize("boundary", ["qualification", "final-b5"])
def test_v2_preemission_rejects_identical_bytes_new_inode_until_admission(
    v2_preemission_rendezvous, monkeypatch, boundary
):
    x = v2_preemission_rendezvous
    for role, raw in x.roles:
        x.publish(role, raw)
    path = x.directory / (x.roles[0][0] + ".json")
    original_inode = path.stat().st_ino

    def replace_same_bytes():
        raw = path.read_bytes()
        path.rename(x.directory / "retained-original.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            assert os.write(fd, raw) == len(raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        assert path.stat().st_ino != original_inode and path.read_bytes() == raw

    if boundary == "qualification":
        genuine = x.proof.current

        def qualify(**kw):
            replace_same_bytes()
            return genuine(**kw)

        monkeypatch.setattr(x.proof, "current", qualify)
    else:
        genuine = x.apparatus._envelope

        def envelope(raw):
            result = genuine(raw)
            replace_same_bytes()
            return result

        x.apparatus._envelope = envelope
    with pytest.raises(executor.ExecutorBridgeError, match="HOST_PROOF_CHANGED"):
        executor.ExecutorNativeBootstrap.current(
            x.apparatus,
            context=x.single.context,
            action="execute",
            run_id=x.run_id,
            phase="BARRIER",
        )
    assert not x.apparatus.emission_admitted
    assert x.single.context.channel.position == 5
    assert executor.load_existing_state_readonly(x.single.store, x.run_id).has(executor.ART_ARMED)


def test_v2_preemission_original_deadline_after_final_freshness(
    v2_preemission_rendezvous, monkeypatch
):
    x = v2_preemission_rendezvous
    for role, raw in x.roles:
        x.publish(role, raw)
    genuine = x.proof.check_current_freshness

    def final_clock(**kw):
        result = genuine(**kw)
        x.elapsed[0] = x.apparatus.preparation_deadline
        return result

    monkeypatch.setattr(x.proof, "check_current_freshness", final_clock)
    with pytest.raises(executor.ExecutorBridgeError, match="EMISSION_NOT_PREPARED"):
        executor.ExecutorNativeBootstrap.current(
            x.apparatus,
            context=x.single.context,
            action="execute",
            run_id=x.run_id,
            phase="BARRIER",
        )
    assert not x.apparatus.emission_admitted
    assert x.single.context.channel.position == 5
    assert executor.load_existing_state_readonly(x.single.store, x.run_id).has(executor.ART_ARMED)


def v2_install_separate_operator_authorization(fixture):
    """Install an explicit ephemeral pair approval before any real grant.

    This fixture exercises the existing fixed verifier boundary; its small
    synthetic authorization is not an operational signed approval format.
    Process/B5 providers retain their existing explicit synthetic limitations.
    """
    mod, rig, source = fixture.module, fixture.rig, fixture.source.source
    raw = mod.op.sql.canonical_bytes({"fixture": "independent-operator-approval"})
    created = rig.store_file("operator-approval", raw, "s11-ex10-synthetic-authorization-v1")
    binding = mod.op.HostArtifactBinding("authorization", created.reference, created.store)
    original = rig.policy_options["approval_verifier"]
    calls = []

    def approve_pair(**kw):
        original(**kw)
        assert kw["canonical_authorization"] == raw
        assert kw["authorization_reference"] == binding.reference
        assert kw["reference"] == mod.op.sql.canonical_bytes(rig.host_policy.reference)
        assert kw["journal"] is rig.a.journal
        calls.append((kw["authorization_reference"], kw["reference"]))

    rig.policy_options = dict(
        rig.policy_options,
        authorization_binding=binding,
        approval_verifier=approve_pair,
    )
    rig.host_policy = mod.op.NativeHostPolicy(**rig.policy_options)
    rig.policy = binding
    source.policy = rig.host_policy
    fixed = tuple(b for name, b in source.registry.bindings.items() if name != "authorization")
    source.registry = mod.op.HostArtifactRegistry(fixed + (binding,))
    source.verify_registry(canonical_binding=rig.a.journal.binding, journal=rig.a.journal)
    request = mod.wire.parse_request_bytes(rig.raw).to_dict()
    request["authorization_reference"] = mod.op.sql.parse_canonical(binding.reference)
    request["capability"]["authorization_reference"] = request["authorization_reference"].copy()
    rig.raw = mod.builder.signed_request(request, rig.a.bridge, rig.a.holder)
    assert source.authorization_reference == binding.reference
    assert rig.host_policy.reference != rig.host_policy.operator_authorization_reference
    return SimpleNamespace(binding=binding, raw=raw, calls=calls)


def test_v2_separate_authorization_actual_start9(v2_native_host, dataset):
    pair = v2_install_separate_operator_authorization(v2_native_host)
    rig = v2_native_start_setup(v2_native_host, dataset)
    result, errors, responses = v2_run_native_host(rig, lambda: v2_start_call(rig))
    assert errors == [] and len(responses) == 1
    assert result["run_id"] == rig.context.proposal["run_id"]
    assert rig.context.channel.position == 9 and rig.context.finished
    record = rig.context.reservation
    assert record["operator_authorization_reference"] == json.loads(pair.binding.reference)
    assert record["coordination_policy_reference"] == v2_native_host.rig.host_policy.reference
    assert record["operator_authorization_reference"] != record["coordination_policy_reference"]
    assert v2_native_host.rig.a.transport.calls.count("RESERVE") == 1
    assert v2_native_host.source.source.publisher.transport.calls.count("B3_CAS") == 1
    assert v2_native_host.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert pair.calls and all(operator != policy for operator, policy in pair.calls)


@pytest.mark.parametrize("provider", ["prior_reader", "prior_verifier", "guard"])
def test_v2_separate_authorization_retains_response_before_provider(
    v2_native_host, dataset, provider
):
    v2_install_separate_operator_authorization(v2_native_host)
    rig = v2_native_start_setup(v2_native_host, dataset)
    original = getattr(rig.context, provider)
    changed = []

    def mutate(**kw):
        result = original(**kw)
        context = kw["context"]
        if context.reservation is not None and not changed:
            value = json.loads(canonical_json_bytes(context.reservation))
            value["coordination_policy_reference"]["sha256"] = "e" * 64
            context.reservation = rig.fixture.module.cf.coord._seal_record(value)
            # The replacement passes the actual closed schema and digest checks.
            executor.wire.qualify_reservation(canonical_json_bytes(context.reservation))
            changed.append(True)
        return result

    setattr(rig.context, provider, mutate)
    with pytest.raises(executor.ExecutorBridgeError, match="RESERVATION_MISMATCH"):
        v2_run_native_host(rig, lambda: v2_start_call(rig))
    assert changed == [True]
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 0
    assert rig.fixture.source.source.publisher.transport.calls.count("B3_CAS") == 0
    assert not (
        rig.store.root / "runs"
    ).exists(), "Provider replaced authenticated intent before delivery"


@pytest.mark.parametrize("fault", ["raw-changed", "registry-rebound", "approval-boolean"])
def test_v2_separate_authorization_pair_approval_rechecks_actual_binding(v2_native_host, fault):
    pair = v2_install_separate_operator_authorization(v2_native_host)
    policy = v2_native_host.rig.host_policy
    original = policy.approve
    changed = []

    def mutate(**kw):
        original(**kw)
        if fault == "raw-changed":
            Path(json.loads(pair.binding.reference)["path"]).write_bytes(b"{}\n")
        elif fault == "registry-rebound":
            policy._authorization_registry = None
        changed.append(True)
        return True if fault == "approval-boolean" else None

    policy.approve = mutate
    with pytest.raises(v2_native_host.module.op.OperationalError):
        policy.qualify(
            native=v2_native_host.native.qualifier.qualify(), journal=v2_native_host.rig.a.journal
        )
    assert changed == [True]
    assert v2_native_host.rig.a.transport.calls.count("RESERVE") == 0
    assert v2_native_host.source.source.publisher.transport.calls.count("B3_CAS") == 0


@pytest.mark.parametrize("fault", ["clear", "rewrite", "reader-rebind", "claim-rebind"])
def test_v2_separate_authorization_intent_mirror_cannot_reset(v2_native_host, dataset, fault):
    v2_install_separate_operator_authorization(v2_native_host)
    rig = v2_native_start_setup(v2_native_host, dataset)
    original = rig.context.guard
    changed = []

    def mutate(**kw):
        original(**kw)
        context = kw["context"]
        if context._reservation_intent is not None and not changed:
            if fault == "clear":
                context._reservation_intent = None
            elif fault == "rewrite":
                value = json.loads(canonical_json_bytes(context.reservation))
                value["coordination_policy_reference"]["sha256"] = "e" * 64
                value = rig.fixture.module.cf.coord._seal_record(value)
                executor.wire.qualify_reservation(canonical_json_bytes(value))
                context.reservation = value
                context._reservation_intent = canonical_json_bytes(
                    {
                        k: value[k]
                        for k in (*executor.wire.RESERVATION_INTENT_FIELDS, "reservation_digest")
                    }
                )
            elif fault == "reader-rebind":
                context._intent_reader = lambda: context._reservation_intent
            else:
                context._retain_reservation_intent = lambda raw: None
            changed.append(True)

    rig.context.guard = mutate
    with pytest.raises(executor.ExecutorBridgeError, match="RESERVATION_MISMATCH"):
        v2_run_native_host(rig, lambda: v2_start_call(rig))
    assert changed == [True]
    assert rig.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert rig.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 0
    assert not (
        rig.store.root / "runs"
    ).exists(), "Retained intent mirror was reset before delivery"


def test_v2_separate_authorization_cannot_fall_back_before_qualification(v2_native_host):
    v2_install_separate_operator_authorization(v2_native_host)
    policy = v2_native_host.rig.host_policy
    policy._authorization_registry = None
    with pytest.raises(v2_native_host.module.op.OperationalError, match="HOST_POLICY_INVALID"):
        policy.qualify(
            native=v2_native_host.native.qualifier.qualify(), journal=v2_native_host.rig.a.journal
        )
    assert v2_native_host.rig.a.transport.calls.count("RESERVE") == 0


def test_v2_separate_authorization_original_policy_single5(v2_native_host, dataset):
    pair = v2_install_separate_operator_authorization(v2_native_host)
    started = v2_native_start_setup(v2_native_host, dataset)
    _, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert errors == [] and len(responses) == 1
    original_policy = started.context.reservation["coordination_policy_reference"].copy()
    single = v2_native_single_setup(started, action="execute")
    try:
        _, replies = v2_run_single_host(
            single,
            lambda: single.context.authorize_step(
                single.store,
                action="execute",
                run_id=started.context.proposal["run_id"],
                git_sha=started.context.proposal["git_sha"],
                phase="BASELINE",
            ),
        )
        assert len(replies) == 1 and single.context.channel.position == 5
        assert single.context.finished and single.context.step_authorized
        assert single.context.reservation["coordination_policy_reference"] == original_policy
        assert single.context.reservation["operator_authorization_reference"] == json.loads(
            pair.binding.reference
        )
        assert single.context.preflight is None
        assert v2_native_host.rig.a.transport.calls.count("RESERVE") == 1
    finally:
        single.context.channel.close()
        single.host.close()


def test_v2_separate_authorization_single_admission_keeps_permission_false(v2_native_host, dataset):
    v2_install_separate_operator_authorization(v2_native_host)
    started = v2_native_start_setup(v2_native_host, dataset)
    _, errors, replies = v2_run_native_host(started, lambda: v2_start_call(started))
    assert errors == [] and len(replies) == 1
    single = v2_native_single_setup(started, action="execute")
    original = single.context.prior_verifier
    changed = []

    def mutate(**kw):
        original(**kw)
        context = kw["context"]
        context._intent_reader = lambda: None
        context._retain_reservation_intent = lambda raw: None
        context._reservation_intent = None
        changed.append(True)

    single.context.prior_verifier = mutate
    try:
        with pytest.raises(executor.ExecutorBridgeError, match="RESERVATION_MISMATCH"):
            v2_run_single_host(
                single,
                lambda: single.context.authorize_step(
                    single.store,
                    action="execute",
                    run_id=started.context.proposal["run_id"],
                    git_sha=started.context.proposal["git_sha"],
                    phase="BASELINE",
                ),
            )
        assert changed == [True]
        assert (
            not single.context.step_authorized
        ), "Rejected final SINGLE guard left scientific permission set"
    finally:
        single.context.channel.close()
        single.host.close()


def test_v2_separate_authorization_envelope_provider_cannot_rebind_mode(v2_native_host):
    v2_install_separate_operator_authorization(v2_native_host)
    policy = v2_native_host.rig.host_policy
    original = policy.envelope
    changed = []

    def mutate(**kw):
        original(**kw)
        policy._authorization_registry = None
        policy._authorization_guard = lambda: None
        changed.append(True)

    policy.envelope = mutate
    mod, a = v2_native_host.module, v2_native_host.rig.a
    with pytest.raises(mod.op.OperationalError, match="HOST_POLICY_INVALID"):
        policy.reserve_references(
            registry=v2_native_host.source.source.registry,
            native=v2_native_host.native.qualifier.qualify(),
            snapshot=v2_native_host.source.source.read_authority(),
            issued_at=mod.cf.coord._utc(a.now()),
        )
    assert changed == [True]
    assert a.transport.calls.count("RESERVE") == 0


def v2_clone_context_options(context):
    cfg = context._cfg()
    return dict(
        action=context.action,
        channel=context.channel,
        bridge_pin=context.pin,
        witness_pin=context.witness,
        holder_key=context.holder,
        expected_challenge=cfg["expected_challenge"],
        request_payload=cfg["payload"],
        evidence_root=cfg["evidence_root"],
        host_evidence_root=cfg["host_evidence_root"],
        staging_name=cfg["staging_name"],
        authority_revision=cfg["authority_revision"],
        host_root_binding_sha256=cfg["host_root_binding_sha256"],
        expected_reservation=cfg["expected_reservation"],
        native_admitter=context.admit,
        current_guard=context.guard,
        common_prior_reader=context.prior_reader,
        common_prior_verifier=context.prior_verifier,
        commit_verifier=context.verify_commit,
        terminal_preparer=context.prepare_terminal,
        clock=context.clock,
    )


def test_v2_separate_authorization_nonstart_constructor_retains_before_admitter(
    v2_native_host, dataset
):
    v2_install_separate_operator_authorization(v2_native_host)
    started = v2_native_start_setup(v2_native_host, dataset)
    _, errors, replies = v2_run_native_host(started, lambda: v2_start_call(started))
    assert errors == [] and len(replies) == 1
    single = v2_native_single_setup(started, action="execute")
    original = single.context.admit
    changed = []

    def mutate(**kw):
        original(**kw)
        context = kw["context"]
        context._intent_reader = lambda: None
        context._retain_reservation_intent = lambda raw: None
        context._reservation_intent = None
        changed.append(True)

    options = dict(v2_clone_context_options(single.context), native_admitter=mutate)
    try:
        with pytest.raises(executor.ExecutorBridgeError, match="RESERVATION_MISMATCH"):
            executor.ExecutorOperationalContext(**options)
        assert changed == [True]
        assert single.context.channel.position == 0
        assert not single.context.step_authorized
    finally:
        single.context.channel.close()
        single.host.close()


def test_v2_separate_authorization_public_result_provider_cannot_clear_intent(
    v2_native_host, dataset
):
    v2_install_separate_operator_authorization(v2_native_host)
    started = v2_native_start_setup(v2_native_host, dataset)
    result, errors, replies = v2_run_native_host(started, lambda: v2_start_call(started))
    assert errors == [] and len(replies) == 1
    original = started.context.guard
    changed = []

    def mutate(**kw):
        original(**kw)
        context = kw["context"]
        context._intent_reader = lambda: None
        context._retain_reservation_intent = lambda raw: None
        context._reservation_intent = None
        changed.append(True)

    started.context.guard = mutate
    with pytest.raises(executor.ExecutorBridgeError, match="RESERVATION_MISMATCH"):
        started.context.persist_public_result(started.store, result)
    assert changed == [True]
    output = started.store.root / (
        "executor-result." + started.context.channel.exchange_id + ".json"
    )
    assert not output.exists(), "Public result bypassed the retained original intent"


def test_v2_separate_authorization_terminal_provider_cannot_clear_intent(
    v2_native_host, dataset, monkeypatch
):
    v2_install_separate_operator_authorization(v2_native_host)
    started = v2_native_start_setup(v2_native_host, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    _, errors, replies = v2_run_native_host(started, lambda: v2_start_call(started))
    assert errors == [] and len(replies) == 1
    single = v2_native_single_setup(started, action="finalize", client_proofs=True)
    v2_client_proof_single_setup(single, monkeypatch)
    changed = []

    def mutate(*, context, store, run_id, result):
        state = executor.load_existing_state_readonly(store, run_id)
        assert state is not None and state.finalized and context is single.context
        executor.validate_finalized_state(
            state.run_root, state.records, custody_hmac_key=store.custody_hmac_key
        )
        context._intent_reader = lambda: None
        context._retain_reservation_intent = lambda raw: None
        context._reservation_intent = None
        changed.append(True)

    single.context.prepare_terminal = mutate
    try:
        with pytest.raises(executor.ExecutorBridgeError, match="RESERVATION_MISMATCH"):
            v2_run_single_host(
                single,
                lambda: executor.finalize_run(
                    single.store,
                    run_id=single.context.proposal["run_id"],
                    git_sha=single.context.proposal["git_sha"],
                    abandon_reason="synthetic-pre-t0-stop",
                    probes=started.probes,
                    operational_context=single.context,
                ),
            )
        assert changed == [True]
        assert (
            not single.context.terminal_prepared
        ), "Rejected terminal callback left permission set"
        assert single.fixture.rig.a.transport.calls.count("RETIRE") == 0
    finally:
        single.context.channel.close()
        single.host.close()


def test_v2_separate_authorization_authenticated_pair_admitted_before_delivery(
    v2_native_host, dataset
):
    pair = v2_install_separate_operator_authorization(v2_native_host)
    rig = v2_native_start_setup(v2_native_host, dataset)
    granted = []

    def reserve():
        permit = None
        try:
            permit = rig.context.reserve(rig.store)
        except executor.ExecutorBridgeError:
            pass
        assert permit is not None, "Authenticated distinct policy/operator pair was rejected"
        granted.append(permit)

    v2_run_native_host(rig, reserve)
    assert len(granted) == 1 and rig.context.reservation["state"] == "PENDING"
    assert rig.context.reservation["operator_authorization_reference"] == json.loads(
        pair.binding.reference
    )
    assert (
        rig.context.reservation["coordination_policy_reference"]
        == v2_native_host.rig.host_policy.reference
    )
    assert not rig.context.consumed
    assert not (rig.store.root / "runs").exists()
    assert v2_native_host.source.source.publisher.transport.calls.count("B3_CAS") == 0


@pytest.mark.parametrize("provider", ["prior_reader", "prior_verifier", "guard"])
def test_v2_separate_authorization_original_intent_denies_before_delivery(
    v2_native_host, dataset, provider
):
    v2_install_separate_operator_authorization(v2_native_host)
    rig = v2_native_start_setup(v2_native_host, dataset)
    original = getattr(rig.context, provider)
    changed, denied = [], []

    def mutate(**kw):
        result = original(**kw)
        context = kw["context"]
        if context.reservation is not None and not changed:
            value = json.loads(canonical_json_bytes(context.reservation))
            value["coordination_policy_reference"]["sha256"] = "e" * 64
            context.reservation = rig.fixture.module.cf.coord._seal_record(value)
            executor.wire.qualify_reservation(canonical_json_bytes(context.reservation))
            changed.append(True)
        return result

    setattr(rig.context, provider, mutate)

    def reserve():
        try:
            rig.context.reserve(rig.store)
        except executor.ExecutorBridgeError as error:
            denied.append(error.code)
        assert denied == [
            "RESERVATION_MISMATCH"
        ], "Altered original intent was admitted before delivery"

    v2_run_native_host(rig, reserve)
    assert changed == [True] and not rig.context.consumed
    assert not (rig.store.root / "runs").exists()
    assert v2_native_host.rig.a.transport.calls.count("RESERVE") == 1
    assert v2_native_host.source.source.publisher.transport.calls.count("B3_CAS") == 0


def test_v2_separate_authorization_retains_intent_after_real_commit_eof(v2_native_host, dataset):
    v2_install_separate_operator_authorization(v2_native_host)
    changed, denied = [], []
    context_ref = {}

    def channel_guard_factory():
        def mutate():
            context = context_ref.get("context")
            if context is None:
                return
            channel = context.channel
            if channel.position == 9 and channel.write_fd is None and not changed:
                context._intent_reader = lambda: None
                context._retain_reservation_intent = lambda raw: None
                context._reservation_intent = None
                changed.append(True)

        return mutate

    rig = v2_native_start_setup(
        v2_native_host, dataset, channel_guard_factory=channel_guard_factory
    )
    context_ref["context"] = rig.context

    def start():
        try:
            v2_start_call(rig)
        except executor.ExecutorBridgeError as error:
            denied.append(error.code)
        assert denied == [
            "UNCERTAIN_PUBLICATION"
        ], "Commit EOF provider erased the retained original intent"

    v2_run_native_host(rig, start)
    assert changed == [True] and not rig.context.finished and not rig.context.scientific_finished
    assert v2_native_host.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert v2_native_host.source.source.publisher.transport.calls.count("B3_CAS") == 1


def _v2_command_refusal_tree(root):
    """Physical immutable SDK evidence, excluding access-time observations."""
    return {
        str(path.relative_to(root)): (
            path.read_bytes() if path.is_file() else None,
            stat.S_IMODE(path.lstat().st_mode),
            path.lstat().st_ino,
            path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns if path.is_file() else None,
        )
        for path in (root, *sorted(root.rglob("*")))
    }


def _v2_command_refusal_actual_sealed_premise(fixture, dataset, monkeypatch):
    """Actual START9 and SDK seal after SINGLE5; common B3 remains OPEN.

    This component premise has no Factory pending intent to clear and no
    terminal publication/retirement. It does not claim actual PID1 transfer.
    """
    started = v2_native_start_setup(fixture, dataset, client_proofs=True)
    v2_client_proof_start_setup(started, monkeypatch)
    result, errors, responses = v2_run_native_host(started, lambda: v2_start_call(started))
    assert not errors and len(responses) == 1 and result["phase"] == "PREPARED"

    def prepare(*, context, store, run_id, result):
        assert context is sealed.context and store is sealed.store
        assert run_id == result["run_id"] == started.context.proposal["run_id"]
        state = executor.load_existing_state_readonly(store, run_id)
        assert state is not None and state.finalized
        executor.validate_finalized_state(
            state.run_root,
            state.records,
            custody_hmac_key=store.custody_hmac_key,
        )

    sealed = v2_native_single_setup(
        started,
        action="finalize",
        client_proofs=True,
        terminal_preparer=prepare,
    )
    v2_client_proof_single_setup(sealed, monkeypatch)
    final, responses = v2_run_single_host(
        sealed,
        lambda: executor.finalize_run(
            sealed.store,
            run_id=result["run_id"],
            git_sha=started.context.proposal["git_sha"],
            abandon_reason="synthetic-pre-t0-stop",
            probes=started.probes,
            operational_context=sealed.context,
        ),
    )
    assert len(responses) == 1 and final["decision"] == "NOT_DEMONSTRATED"
    assert not final["binding"] and sealed.context.scientific_finished
    assert sealed.context.channel.closed and sealed.context.channel.position == 5
    state = executor.load_existing_state_readonly(sealed.store, result["run_id"])
    assert state is not None and state.finalized and sealed.store.verify(result["run_id"]) == []
    assert not state.has(executor.ART_RAW)
    authority = sealed.broker.sources.read_authority()
    assert (
        sealed.fixture.module.op.history.find_run(authority, result["run_id"])["lifecycle_state"]
        == "OPEN"
    )
    assert started.fixture.rig.a.transport.calls.count("RETIRE") == 0
    return started, state, _v2_command_refusal_tree(state.run_root)


@pytest.mark.parametrize(
    "fault",
    [
        pytest.param("none", id="original-scientific2-private-result"),
        pytest.param("closed-original-provider-rebound", id="closed-original-provider-rebound"),
        pytest.param("seal-hmac-after-actual-refusal", id="seal-hmac-after-actual-refusal"),
        pytest.param(
            "custody-lock-replaced-after-metadata-open",
            id="custody-lock-replaced-after-metadata-open",
        ),
        pytest.param(
            "custody-lock-replaced-after-shared-unlock",
            id="custody-lock-replaced-after-shared-unlock",
        ),
    ],
)
def test_v2_native_command_terminal_refusal_requires_original_private_producer(
    v2_empty_native_host, dataset, monkeypatch, capsys, fault
):
    """Real Command/SINGLE5/SDK Ex10Refusal; faults must reach their exact point.

    Old-command causal RED uses SDK512 and the unchanged scientific exception,
    proving the missing private output. Fault regressions qualify only after the
    intended producer injection is reached; missing APIs/imports are not RED.
    Synthetic installed host providers remain explicit. PID1/Docker process
    exit transfer requires a separate actual owned-boundary test.
    """
    started, sealed_state, sealed_tree = _v2_command_refusal_actual_sealed_premise(
        v2_empty_native_host,
        dataset,
        monkeypatch,
    )
    declaration = v2_native_single_setup(started, action="finalize", client_proofs=True)
    v2_client_proof_single_setup(declaration, monkeypatch)
    context, store = declaration.context, declaration.store
    authority_before = declaration.broker.sources.read_authority().canonical_material
    slots_before = declaration.fixture.rig.a.journal.read()["latest"]
    result_path = store.root / ("executor-result." + context.channel.exchange_id + ".json")
    lock_path = store.root / ".s11-custody.lock"
    assert not result_path.exists() and lock_path.is_file()
    probe = {"science": [], "producer": [], "fired": [], "armed": False}
    actual_finalize = executor.finalize_run
    actual_producer = executor.ExecutorOperationalContext.persist_terminal_refusal
    actual_open, actual_flock = os.open, fcntl.flock

    def original_scientific_refusal(*args, **kwargs):
        try:
            return actual_finalize(*args, **kwargs)
        except executor.Ex10Refusal as error:
            assert type(error) is executor.Ex10Refusal and error.terminal
            assert error.reasons == ("run-finalized",)
            assert kwargs["operational_context"] is context
            assert context.finished and context.step_authorized and not context.scientific_finished
            assert context.channel.closed and context.channel.position == 5
            probe["science"].append(error)
            raise

    def replace_lock(point):
        assert probe["armed"] and not probe["fired"] and len(probe["science"]) == 1
        before, raw = lock_path.lstat(), lock_path.read_bytes()
        assert stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o600
        assert before.st_nlink == 1
        if point == "actual-custody-shared-lock-released":
            assert result_path.is_file()
            probe["private_before_late_fault"] = result_path.read_bytes()
            assert probe["private_before_late_fault"] == canonical_json_bytes(
                {"refused": True, "terminal": True, "reasons": ["run-finalized"]},
            )
        replacement = lock_path.with_name(lock_path.name + ".test-owned-replacement")
        with replacement.open("xb") as stream:
            stream.write(raw)
        replacement.chmod(0o600)
        os.replace(replacement, lock_path)
        assert lock_path.read_bytes() == raw and lock_path.lstat().st_ino != before.st_ino
        probe["fired"].append(point)

    def producer(observation, error, *, declare_executor_gone):
        assert observation is context and error is probe["science"][0]
        assert declare_executor_gone is True and not probe["producer"]
        assert context._closed_single_reader() is not None
        probe["producer"].append(error)
        probe["armed"] = True
        if fault == "closed-original-provider-rebound":
            original = context.guard
            context.guard = lambda **kw: original(**kw)
            assert context.guard is not original
            probe["fired"].append("original-provider-identity-after-actual-refusal")
        elif fault == "seal-hmac-after-actual-refusal":
            path = sealed_state.run_root / ".s11-finalized"
            value = json.loads(path.read_bytes())
            assert value["signature"] != "0" * 64
            value["signature"] = "0" * 64
            path.write_bytes(canonical_json_bytes(value))
            with pytest.raises(executor.EvidencePolicyError):
                executor.validate_finalized_state(
                    sealed_state.run_root,
                    store.records(sealed_state.run_id),
                    custody_hmac_key=store.custody_hmac_key,
                )
            probe["fired"].append("actual-seal-hmac-after-actual-refusal")
        try:
            return actual_producer(observation, error, declare_executor_gone=declare_executor_gone)
        finally:
            probe["armed"] = False

    def observe_open(path, flags, *args, **kwargs):
        fd = actual_open(path, flags, *args, **kwargs)
        if (
            fault == "custody-lock-replaced-after-metadata-open"
            and probe["armed"]
            and not probe["fired"]
            and path == ".s11-custody.lock"
            and flags & os.O_PATH
        ):
            assert os.readlink("/proc/self/fd/" + str(kwargs["dir_fd"])) == str(store.root)
            assert os.fstat(fd).st_ino == lock_path.lstat().st_ino
            replace_lock("actual-custody-metadata-fd-opened")
        return fd

    def observe_flock(fd, operation):
        result = actual_flock(fd, operation)
        if (
            fault == "custody-lock-replaced-after-shared-unlock"
            and probe["armed"]
            and not probe["fired"]
            and operation == fcntl.LOCK_UN
            and os.readlink("/proc/self/fd/" + str(fd)) == str(lock_path)
        ):
            assert os.fstat(fd).st_ino == lock_path.lstat().st_ino
            replace_lock("actual-custody-shared-lock-released")
        return result

    monkeypatch.setattr(executor, "finalize_run", original_scientific_refusal)
    monkeypatch.setattr(executor.ExecutorOperationalContext, "persist_terminal_refusal", producer)
    monkeypatch.setattr(os, "open", observe_open)
    monkeypatch.setattr(fcntl, "flock", observe_flock)
    monkeypatch.setattr(s11_ex10.Command, "operational_context", lambda self, options: context)
    monkeypatch.setattr(s11_ex10.Command, "_store", staticmethod(lambda: store))
    monkeypatch.setattr(s11_ex10.Command, "probes", lambda self: started.probes)
    output = io.StringIO()
    capsys.readouterr()

    def operation():
        with pytest.raises(CommandError) as error:
            call_command(
                "s11_ex10",
                "finalize",
                "--run-id",
                context.proposal["run_id"],
                "--expected-git-sha",
                context.proposal["git_sha"],
                "--declare-executor-gone",
                "--operational-context-stdin",
                stdout=output,
            )
        return error.value

    error, responses = v2_run_single_host(declaration, operation)
    captured = capsys.readouterr()
    assert len(responses) == 1 and len(probe["science"]) == len(probe["producer"]) == 1
    assert probe["science"][0] is probe["producer"][0]
    assert not output.getvalue() and captured.out == "" and not probe["armed"]
    expected = canonical_json_bytes(
        {"refused": True, "terminal": True, "reasons": ["run-finalized"]}
    )
    if fault == "none":
        assert error.returncode == 2 and str(error) == "S11_EX10_REFUSED"
        assert json.loads(captured.err) == {
            "refused": True,
            "terminal": True,
            "reasons": ["run-finalized"],
        }
        assert not probe["fired"]
        assert result_path.read_bytes() == expected
        info = result_path.lstat()
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_nlink == 1 and info.st_uid == os.geteuid() and info.st_gid == os.getegid()
    else:
        assert len(probe["fired"]) == 1
        assert error.returncode == 3 and str(error) == "S11_EX10_NATIVE_REFUSAL_UNQUALIFIED"
        assert captured.err == ""
        if fault == "custody-lock-replaced-after-shared-unlock":
            assert result_path.read_bytes() == probe["private_before_late_fault"] == expected
        else:
            assert not result_path.exists()
    assert context.finished and context.step_authorized and not context.scientific_finished
    assert (
        not context.terminal_prepared and context.channel.closed and context.channel.position == 5
    )
    assert context.channel.read_fd is context.channel.write_fd is context.channel.error_fd is None
    assert declaration.host.closed
    assert declaration.broker.sources.read_authority().canonical_material == authority_before
    assert declaration.fixture.rig.a.journal.read()["latest"] == slots_before
    assert declaration.fixture.rig.a.transport.calls.count("RESERVE") == 1
    assert declaration.fixture.rig.a.transport.calls.count("COMMIT_OPEN") == 1
    assert declaration.fixture.rig.a.transport.calls.count("RETIRE") == 0
    assert declaration.broker.sources.publisher.transport.calls.count("B3_CAS") == 1
    if fault != "seal-hmac-after-actual-refusal":
        assert _v2_command_refusal_tree(sealed_state.run_root) == sealed_tree


# FINAL admission: complete metadata, lock lifecycle and frozen journal recovery.


@pytest.mark.parametrize("replacement", ["lock", "root"])
def test_final_admission_rechecks_named_custody_after_provider_io(h, replacement):
    h.start()
    root = h.store.root
    displaced = root.with_name(root.name + "-displaced-by-admission-test")
    try:
        with executor._finalize_existing_checkpoint(h.store) as normalize:
            if replacement == "lock":
                lock = root / ".s11-custody.lock"
                lock.rename(root / ".original-custody-lock")
                lock.write_bytes(b"")
                lock.chmod(0o600)
                before = (tree(root), None)
            else:
                root.rename(displaced)
                root.mkdir(mode=0o700)
                before = (tree(root), tree(displaced))
            with pytest.raises(executor.EvidencePolicyError):
                normalize()
            assert (tree(root), tree(displaced) if displaced.exists() else None) == before
        assert (tree(root), tree(displaced) if displaced.exists() else None) == before
        assert h.store._mutation_depth == 0 and h.store._mutation_lock_file is None
    finally:
        if displaced.exists():
            shutil.rmtree(displaced)


def test_finalized_final_refusal_preserves_root_lock_and_all_metadata_before_probes(h):
    h.start()
    h.finalize(abandon_reason="test")
    before = tree(h.store.root)

    def forbidden_dataset():
        pytest.fail("Already-sealed FINAL reached dataset before its pure refusal")

    probes = replace(h.probes, dataset=forbidden_dataset)
    with write_spy() as writes:
        rejected = refusal(
            executor.finalize_run, h.store, run_id=h.run_id, git_sha=SHA, probes=probes
        )
    assert rejected.reasons == ("run-finalized",) and rejected.terminal
    assert writes == [] and tree(h.store.root) == before


def test_final_open_to_sealed_race_rechecks_existing_exclusive_before_mutation(h, monkeypatch):
    h.start()
    original_checkpoint = executor._finalize_existing_checkpoint
    boundary = {}

    @contextmanager
    def concurrent_finalizer(store):
        if not boundary:
            # Another admitted FINAL wins between the shared view and exclusive
            # acquisition. It uses the actual public FINAL and original writer.
            boundary["closing"] = True
            boundary["result"] = h.finalize(abandon_reason="test")
            boundary["tree"] = tree(store.root)
        with original_checkpoint(store) as normalize:
            yield normalize

    monkeypatch.setattr(executor, "_finalize_existing_checkpoint", concurrent_finalizer)
    rejected = refusal(h.finalize, abandon_reason="test")
    assert rejected.reasons == ("run-finalized",) and rejected.terminal
    assert boundary["result"]["lifecycle"] == "ABANDONED_PRE_T0"
    assert tree(h.store.root) == boundary["tree"]
    assert h.store._mutation_depth == 0 and h.store._mutation_lock_file is None


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "fifo"])
def test_final_existing_lock_invalid_never_creates_or_repairs_custody(h, kind):
    h.start()
    lock = h.store.root / ".s11-custody.lock"
    lock.unlink()
    if kind == "symlink":
        lock.symlink_to("missing-lock-target")
    elif kind == "directory":
        lock.mkdir()
    elif kind == "fifo":
        os.mkfifo(lock, 0o600)
    before = tree(h.store.root)
    with pytest.raises((OSError, executor.EvidencePolicyError)):
        with executor._finalize_existing_checkpoint(h.store):
            pytest.fail("Invalid existing custody lock admitted FINAL")
    assert tree(h.store.root) == before
    assert h.store._mutation_depth == 0 and h.store._mutation_lock_file is None


def test_final_existing_checkpoint_reentry_and_exception_release_original_lock(h):
    h.start()
    before = tree(h.store.root)
    with pytest.raises(RuntimeError, match="injected-admission-error"):
        with executor._finalize_existing_checkpoint(h.store):
            held = h.store._mutation_lock_file
            with executor._finalize_existing_checkpoint(h.store):
                assert h.store._mutation_lock_file is held and not held.closed
                raise RuntimeError("injected-admission-error")
    assert held.closed and h.store._mutation_lock_file is None
    assert h.store._mutation_depth == 0 and tree(h.store.root) == before
    # A different fd can acquire the original inode immediately after unwind.
    fd = os.open(h.store.root / ".s11-custody.lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@pytest.mark.parametrize("state", ["PREPARED", "COMMITTED"])
def test_final_admission_preserves_original_interrupted_journal_recovery(h, monkeypatch, state):
    h.start()
    commit = h.store._commit_finalization_journal

    def interrupted(path, payload):
        if state == "COMMITTED":
            commit(path, payload)
        raise SystemExit("synthetic-finalization-interruption")

    monkeypatch.setattr(h.store, "_commit_finalization_journal", interrupted)
    with pytest.raises(SystemExit, match="synthetic-finalization-interruption"):
        h.finalize(abandon_reason="test")
    journal = h.store.root / "finalization-transactions" / f"{h.run_id}.json"
    from base64 import b64decode

    original_transaction = json.loads(journal.read_bytes())["payload"]
    run_root = h.store.root / "runs" / h.run_id
    original_records = [
        json.loads(line)
        for line in b64decode(original_transaction["original_index_b64"]).splitlines()
    ]
    decision = next(
        record for record in original_records if record["artifact_type"] == executor.ART_DECISION
    )
    decision_path = run_root / decision["relative_path"]
    decision_before = decision_path.read_bytes()
    before = tree(h.store.root)
    with executor._finalize_existing_checkpoint(h.store):
        plan = executor._finalize_recovery_plan_under_caller_lock(h.store, h.run_id)
        assert plan["state"] == state
    assert tree(h.store.root) == before
    monkeypatch.setattr(h.store, "_commit_finalization_journal", commit)
    rejected = refusal(h.finalize, abandon_reason="test")
    recovered = executor.load_existing_state_readonly(h.store, h.run_id)
    if state == "PREPARED":
        # Recovery keeps the already-custodied decision and the original
        # DECIDED-phase abandonment refusal; it does not promise re-finalization.
        assert rejected.reasons == ("phase:DECIDED:abandon-after-t0-not-allowed",)
        assert not rejected.terminal and not recovered.finalized
        assert executor.phase_of(recovered) is executor.Phase.DECIDED
        index = run_root / "metadata" / "artifacts.jsonl"
        for path, key in (
            (index, "original_index_b64"),
            (index.with_suffix(index.suffix + ".hmac"), "original_index_signature_b64"),
            (run_root / "SHA256SUMS", "original_checksums_b64"),
        ):
            encoded = original_transaction[key]
            if encoded is None:
                assert not path.exists()
            else:
                assert path.read_bytes() == b64decode(encoded)
        assert not (run_root / original_transaction["target_relative_path"]).exists()
        assert not (run_root / original_transaction["seal_relative_path"]).exists()
    else:
        assert rejected.reasons == ("run-finalized",) and rejected.terminal
        assert recovered.finalized
    assert decision_path.read_bytes() == decision_before
    assert not journal.exists()
    assert h.store._mutation_depth == 0 and h.store._mutation_lock_file is None


@pytest.mark.parametrize("fault", ["target-escape", "index-base64", "raw-symlink"])
def test_final_invalid_prepared_journal_rejects_before_custody_normalization(h, fault):
    from base64 import b64encode

    h.start()
    run_root = h.store.root / "runs" / h.run_id
    index = run_root / "metadata" / "artifacts.jsonl"
    payload = {
        "state": "PREPARED",
        "run_id": h.run_id,
        "seal_relative_path": ".s11-finalized",
        "target_relative_path": "metadata/EX-10/run-manifest.final.json",
        "original_index_b64": b64encode(index.read_bytes()).decode(),
        "original_index_signature_b64": None,
        "original_checksums_b64": None,
    }
    if fault == "target-escape":
        payload["target_relative_path"] = "../foreign-run.json"
    elif fault == "index-base64":
        payload["original_index_b64"] = []
    else:
        raw = run_root / "raw"
        raw.rmdir()
        raw.symlink_to("../missing-raw-root")
    parent = h.store.root / "finalization-transactions"
    parent.mkdir(mode=0o700)
    h.store._write_finalization_journal(parent / f"{h.run_id}.json", payload, replace=False)
    before = tree(h.store.root)
    with pytest.raises(executor.EvidencePolicyError):
        h.finalize(abandon_reason="test")
    assert tree(h.store.root) == before
    assert h.store._mutation_depth == 0 and h.store._mutation_lock_file is None
