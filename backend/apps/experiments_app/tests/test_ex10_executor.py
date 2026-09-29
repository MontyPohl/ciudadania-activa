"""EX-10 fase 3A: executor — emisión, transporte, drain, primer run válido, custodia y crash.

LOCAL TEST / NON-EXPERIMENTAL. Cada run va contra un backend simulado con sockets TCP reales
de la biblioteca estándar en 127.0.0.1 (nunca internet ni el stack S11), con una custodia
temporal bajo `.s11-test-artifacts`. Los `run_id` son sintéticos de esa custodia de test; el
modo OFFICIAL sólo se usa aquí para probar la regla del primer run válido sobre ella.
Los números entre paréntesis son los ítems de §47, §48 y §53–§57 de la fase 3A.
"""

from __future__ import annotations

import io
import json
import os
import signal
import socket
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError

from apps.experiments_app import ex10_executor as executor
from apps.experiments_app import ex10_load as ex10
from apps.experiments_app import ex10_worker_probe
from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.evidence import EvidenceError
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
