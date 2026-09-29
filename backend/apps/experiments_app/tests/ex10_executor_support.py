"""Soporte de los tests del executor de EX-10 (fase 3A). LOCAL TEST / NON-EXPERIMENTAL.

Runs, telemetría y base sintéticos coherentes con G0, un backend HTTP simulado con sockets
reales de la biblioteca estándar en 127.0.0.1 (nunca internet) y las sondas del executor.
Nada de esto toca el stack S11, `var/s11-runs`, un proveedor ni un run oficial.
"""

from __future__ import annotations

import json
import shutil
import socket
import socketserver
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from apps.experiments_app import ex10_executor as executor
from apps.experiments_app import ex10_load as ex10
from apps.experiments_app import ex10_preflight as pf
from apps.experiments_app import ex10_reconcile as rec
from apps.experiments_app.canonical import canonical_json_bytes, sha256_bytes
from apps.experiments_app.evidence import EvidenceStore

SHA = "a" * 40
T0 = 10_000_000_000_000
T0_UTC = 1_790_000_000_000_000_000  # septiembre de 2026, en ns desde la época
MS = 1_000_000
US = 1_000
RUN_ID = "s11-11111111-1111-5111-8111-111111111111"
API = rec.ProcessRef("backend-api", 11)
WORKER = rec.ProcessRef("worker-node", 22)
HMAC_KEY = b"k" * 32
PUBLIC_KEY = b"\x01" * 32
BOOT_ID = "00000000-0000-4000-8000-000000000000"
ARTIFACTS = Path.cwd() / ".s11-test-artifacts"


def iso(ns: int) -> str:
    moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=ns // US)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def contract_body(report_id: str) -> bytes:
    reward = {"impact_points": 20, "credits": 20, "status": "PENDIENTE"}
    return json.dumps({"id": report_id, "status": "CREADO", "reward": reward}).encode()


def report_id_for(request_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"report:{request_key}"))


def task_id_for(request_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"task:{request_key}"))


def process(ref: rec.ProcessRef) -> dict[str, Any]:
    return {"hostname": ref.hostname, "pid": ref.pid}


# --- Runs sintéticos (sin hilos ni sockets) --------------------------------------------------


def exchange(item, start, end, *, status=201, body=None, error=None, phase="receive"):
    """Un `Exchange` del intento, en ns relativos a T0. Por defecto, un 201 contractual.

    Con `error`, un fallo de transporte capturado; `status` sólo si llegó la línea de estado
    (un 201 truncado), igual que `StdlibHttpTransport`.
    """
    times = (T0 + start, T0 + end, T0_UTC + start, T0_UTC + end)
    if error is not None:
        return ex10.Exchange(
            *times, http_status=status, error_class=error, error_detail="Error", error_phase=phase
        )
    if body is None and status == 201:
        body = contract_body(report_id_for(item.request_key))
    return ex10.Exchange(*times, http_status=status, body=b"{}" if body is None else body)


def ms(duration, status=201, **kwargs):
    """Override: el intento dura `duration` ms desde su inicio en lazo cerrado."""
    return lambda item, start, end: exchange(
        item, start, start + duration * MS, status=status, **kwargs
    )


def at(start_ms, end_ms, **kwargs):
    """Override: el intento empieza y termina en esos ms desde t0 (sin lazo cerrado)."""
    return lambda item, start, end: exchange(item, start_ms * MS, end_ms * MS, **kwargs)


def synthetic_run(plan, *, overrides=None, duration=50 * MS, faults=(), interrupted=False):
    """Un run completo en lazo cerrado: cada ítem dura `duration` y el siguiente sigue sin
    pausa. `overrides[request_key]` es un callable `(item, start, end) -> Exchange`."""
    overrides = overrides or {}
    records = {}
    for vu in plan.vus:
        cursor = 0
        for item in plan.for_vu(vu):
            make = overrides.get(item.request_key)
            exch = make(item, cursor, cursor + duration) if make else None
            exch = exch or exchange(item, cursor, cursor + duration)
            records[item.request_key] = ex10.attempt_record(item, exch, t0_ns=T0)
            cursor = max(cursor, exch.t_end_ns - T0) + 1
    return ex10.GeneratorRun(
        plan_sha256=plan.sha256,
        t0_ns=T0,
        t0_utc_ns=T0_UTC,
        attempts=tuple(records[item.request_key] for item in plan.items),
        faults=tuple(faults),
        interrupted=interrupted,
    )


def timeline(attempt) -> dict[str, int]:
    """Instantes UTC coherentes con G0 dentro del intervalo HTTP del intento:
    `created ≤ enqueue ≤ http.finished`, `enqueue ≤ started ≤ finished` (µs exactos)."""
    start, end = attempt.t_start_utc_ns, attempt.t_end_utc_ns
    quarter = (end - start) // 4 // US * US
    return {
        "created": start + quarter,
        "enqueue": start + 2 * quarter,
        "http_finished": end - quarter,
        "started": start + 2 * quarter + quarter // 2 // US * US,
        "finished": start + 4 * quarter,
    }


_ACCEPTED_LIKE = {ex10.AttemptOutcome.HTTP_ACCEPTED}
_RESPONDED = {
    ex10.AttemptOutcome.HTTP_ACCEPTED,
    ex10.AttemptOutcome.CONTRACT_VIOLATION,
    ex10.AttemptOutcome.HTTP_REJECTED,
}


def database_for(plan, run, *, mutate=None) -> rec.DatabaseSnapshot:
    """La base coherente con los 201 contractuales: un Report clasificado por éxito."""
    by_key = plan.by_key()
    rows = {}
    for attempt in run.attempts:
        if attempt.outcome not in _ACCEPTED_LIKE:
            continue
        item = by_key[attempt.request_key]
        row = rec.ReportRow(
            report_id=attempt.report_id,
            citizen_id=item.dataset_user_id,
            status="CLASIFICADO",
            created_at_utc_ns=timeline(attempt)["created"],
            media_hashes=(item.image_sha256,),
            created_rewards=((20, 20, "PENDIENTE"),),
            created_audit_rows=1,
            classified_audit_rows=1,
            classification_models=(ex10.AI_MODEL_NAME,),
            status_history=(("", "CREADO"), ("CREADO", "CLASIFICADO")),
        )
        if mutate:
            row = mutate(attempt.request_key, row)
        if row is not None:
            rows[row.report_id] = row
    return rec.DatabaseSnapshot(rows)


def events_for(plan, run, *, run_id=RUN_ID, mutate=None) -> tuple[list[dict], list[dict]]:
    """Telemetría coherente: http.finished por respuesta y queued/started/finished por 201."""
    by_key = plan.by_key()
    http, celery = [], []
    base = {
        "schema_version": rec.TELEMETRY_SCHEMA_VERSION,
        "run_id": run_id,
        "scenario_id": "EX-10",
    }
    for attempt in run.attempts:
        if attempt.outcome not in _RESPONDED:
            continue
        item = by_key[attempt.request_key]
        at = timeline(attempt)
        events = [
            {**base, "event": "http.finished", "request_id": item.request_id,
             "finished_at": iso(at["http_finished"]), "http_status": attempt.http_status,
             "process": process(API)},
        ]  # fmt: skip
        if attempt.outcome in _ACCEPTED_LIKE:
            task = {**base, "request_id": item.request_id, "task_id": task_id_for(item.request_key),
                    "task_name": ex10.TASK_NAME}  # fmt: skip
            events += [
                {**task, "event": "celery.queued", "enqueue_at": iso(at["enqueue"]),
                 "status": "queued", "db_transaction_open": False, "process": process(API)},
                {**task, "event": "celery.started", "start_at": iso(at["started"]),
                 "status": "started", "process": process(WORKER)},
                {**task, "event": "celery.finished", "start_at": iso(at["started"]),
                 "finish_at": iso(at["finished"]), "status": "finished",
                 "celery_state": "success", "process": process(WORKER)},
            ]  # fmt: skip
        if mutate:
            events = mutate(attempt.request_key, events)
        for event in events:
            (http if event["event"] == "http.finished" else celery).append(event)
    return http, celery


def stream(events, name, run_id=RUN_ID) -> rec.Stream:
    raw = b"".join(canonical_json_bytes(e) for e in events)
    return rec.parse_stream(raw, name=name, run_id=run_id)


def expected_config(**changes) -> rec.ConfigObservation:
    return replace(
        rec.ConfigObservation(
            api_task_always_eager=False,
            broker_url=ex10.BROKER_URL,
            worker_nodes=1,
            worker_max_concurrency=ex10.WORKER_CONCURRENCY,
            worker_task_always_eager=False,
            worker_fake_ai_enabled=True,
            worker_ai_adapter=ex10.AI_MODEL_NAME,
        ),
        **changes,
    )


@dataclass
class Scenario:
    """Todo lo que la reconciliación lee, coherente por defecto (un PASS)."""

    plan: Any
    run: Any
    database: rec.DatabaseSnapshot | None
    http: rec.Stream
    celery: rec.Stream
    before: rec.ConfigObservation | None
    after: rec.ConfigObservation | None
    broker_dynamic: int | None = 0

    def reconcile(self) -> rec.Reconciliation:
        return rec.reconcile(
            self.plan,
            self.run,
            database=self.database,
            http=self.http,
            celery=self.celery,
            config_before=self.before,
            config_after=self.after,
            broker_dynamic=self.broker_dynamic,
        )

    def evaluate(self) -> ex10.Ex10Evaluation:
        result = self.reconcile()
        return ex10.evaluate_run(
            self.plan,
            self.run,
            persisted=result.persisted,
            async_evidence=result.async_evidence,
            environment_invalidity=result.environment_invalidity,
        )


def scenario(plan, run=None, *, db_mutate=None, events_mutate=None, **changes) -> Scenario:
    run = run or synthetic_run(plan)
    http, celery = events_for(plan, run, mutate=events_mutate)
    base = Scenario(
        plan=plan,
        run=run,
        database=database_for(plan, run, mutate=db_mutate),
        http=stream(http, "http"),
        celery=stream(celery, "celery"),
        before=expected_config(),
        after=expected_config(),
    )
    return replace(base, **changes)


# --- Evidencia de tests ------------------------------------------------------------------------


def make_store(name: str) -> EvidenceStore:
    """Una custodia de test bajo `.s11-test-artifacts` (la custodia no admite /tmp)."""
    base = ARTIFACTS / f"ex10-executor-{name}"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    return EvidenceStore(
        base / "evidence",
        custody_hmac_key=HMAC_KEY,
        publication_verification_key=PUBLIC_KEY,
        rolling_checksums=False,
    )


def remove_store(store: EvidenceStore) -> None:
    shutil.rmtree(store.root.parent, ignore_errors=True)


def preflight_report(*, git_sha=SHA, checked_at: datetime | None = None, **changes) -> dict:
    """Un informe de preflight PASS completo, con la forma exacta de `PreflightReport`."""
    names = [n for n in pf.REQUIRED_CHECKS if n != "ready_state"] + ["ready_state:reports"]
    report = {
        "schema_version": pf.SCHEMA_VERSION,
        "scenario_id": "EX-10",
        "protocol_version": ex10.PROTOCOL_ID,
        "methodology_version": ex10.METHODOLOGY_VERSION,
        "result": "PASS",
        "gate": "READY_FOR_EXECUTOR_INTEGRATION",
        "ready_for_execution": False,
        "run_id": None,
        "evidence_written": False,
        "report_posts": 0,
        "provider_calls": 0,
        "task_publishes": 0,
        "health_requests": 1,
        "control_probes": 2,
        "expected_git_sha": git_sha,
        "git_sha": git_sha,
        "checked_at": iso(rec.datetime_ns(checked_at or datetime.now(UTC))),
        "checks": [
            {"check": n, "result": "PASS", "expected": None, "observed_sanitized": None,
             "detail_code": None}
            for n in names
        ],
    }  # fmt: skip
    report.update(changes)
    return report


class FakeTokens:
    """Como `EphemeralTokens`: sólo en memoria; `release()` los borra."""

    def __init__(self, aliases):
        self._tokens = {
            alias: f"tok{index:02d}.{uuid.uuid4().hex}{uuid.uuid4().hex}"
            for index, alias in enumerate(aliases, 1)
        }
        self.values = tuple(self._tokens.values())

    def mapping(self):
        if self._tokens is None:
            raise RuntimeError("TOKENS_RELEASED")
        return dict(self._tokens)

    def release(self):
        self._tokens = None

    @property
    def released(self):
        return self._tokens is None


@dataclass(frozen=True)
class TokenOk:
    ok: bool = True


class FakeClock:
    """Reloj monotónico que sólo avanza cuando el drain duerme: 360 s de drain, al instante."""

    def __init__(self, start: int | None = None):
        self.now = start if start is not None else time.monotonic_ns()
        self.sleeps: list[float] = []

    def __call__(self) -> int:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += round(seconds * 1e9)
        time.sleep(0)


# --- Backend HTTP simulado ---------------------------------------------------------------------


class Backend:
    """API y worker simulados sobre un socket TCP real de 127.0.0.1.

    Responde según `modes[request_key]` y, como el stack real, deja la huella de cada
    creación: filas en su «base» y eventos http/celery en los streams del run, tomando el
    run del `X-S11-Run-ID` del request. `http.finished` se escribe antes de responder, como
    la frontera WSGI. Modos: 201, 500, 100-201, 100-500, truncated, slow-headers, slow-body,
    reset, close, malformed y silent.
    """

    CREATES = {"201", "100-201", "truncated", "slow-headers", "slow-body"}

    def __init__(self, store: EvidenceStore, plan, *, delay: float = 0.2):
        self.store = store
        self.plan = plan
        self.delay = delay
        self.by_request = {item.request_id: item for item in plan.items}
        self.modes: dict[str, str] = {}
        self.hooks: dict[str, Callable[[], None]] = {}
        self.telemetry = True
        self.worker = True
        #: El worker sólo llega a un RETRY: la tarea no termina nunca dentro del drain.
        self.retry = False
        self.rows: dict[str, rec.ReportRow] = {}
        self.requests: list[dict[str, Any]] = []
        #: Conexiones TCP aceptadas: una por intento si no hay reutilización de socket.
        self.accepted = 0
        self.lock = threading.Lock()
        self.release = threading.Event()
        backend = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with backend.lock:
                    backend.accepted += 1
                backend.serve(self.request)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True
            request_queue_size = 64

        self.server = Server(("127.0.0.1", 0), Handler)
        self.address = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    @property
    def connections(self) -> int:
        with self.lock:
            return len(self.requests)

    def _read(self, sock):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(65536)
            if not chunk:
                return None
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        headers: dict[str, list[str]] = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers.setdefault(name.strip().lower(), []).append(value.strip())
        length = int(headers.get("content-length", ["0"])[0])
        while len(rest) < length:
            chunk = sock.recv(65536)
            if not chunk:
                break
            rest += chunk
        return lines[0], headers, rest

    def serve(self, sock):
        parsed = self._read(sock)
        if parsed is None:
            return
        request_line, headers, body = parsed
        request_id = headers.get("x-s11-request-id", [""])[0]
        run_id = headers.get("x-s11-run-id", [""])[0]
        item = self.by_request.get(request_id)
        key = item.request_key if item else "unknown"
        with self.lock:
            self.requests.append(
                {"key": key, "line": request_line, "headers": headers, "body": len(body),
                 "body_sha": sha256_bytes(body), "at": time.monotonic_ns(),
                 "peer": sock.getpeername()}
            )  # fmt: skip
        hook = self.hooks.get(key)
        if hook:
            hook()
        mode = self.modes.get(key, "201")
        if mode == "reset":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00" + b"\x00" * 4)
            sock.close()
            return
        if mode == "close":
            return
        if mode == "malformed":
            sock.sendall(b"NOT-HTTP garbage\r\n\r\n")
            return
        if mode == "silent":
            self.release.wait(120)
            return
        status, reason = (
            (500, "Internal Server Error") if mode.endswith("500") else (201, "Created")
        )
        report_id = str(uuid.uuid4())
        created = time.time_ns() // US * US
        if mode in self.CREATES and item is not None:
            self._persist(item, report_id, created, run_id)
        body = contract_body(report_id) if status == 201 else b'{"detail":"error"}'
        declared = len(body) + 50 if mode == "truncated" else len(body)
        if item is not None:
            self._event(run_id, {"event": "http.finished", "request_id": item.request_id,
                                 "finished_at": iso(max(time.time_ns(), created + 2 * US)),
                                 "http_status": status, "process": process(API)})  # fmt: skip
        if mode.startswith("100-"):
            sock.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        head = (
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {declared}\r\nConnection: close\r\n\r\n"
        ).encode()
        if mode == "slow-headers":
            time.sleep(self.delay)
        sock.sendall(head)
        if mode == "slow-body":
            sock.sendall(body[:5])
            time.sleep(self.delay)
            body = body[5:]
        sock.sendall(body)

    def _event(self, run_id, fields):
        if not self.telemetry:
            return
        payload = {"schema_version": rec.TELEMETRY_SCHEMA_VERSION, "run_id": run_id,
                   "scenario_id": "EX-10", **fields}  # fmt: skip
        name = "http" if fields["event"] == "http.finished" else "celery"
        self.store.append_jsonl(run_id=run_id, scenario_id="EX-10", stream=name, payload=payload)

    def _persist(self, item, report_id, created, run_id):
        with self.lock:
            self.rows[report_id] = rec.ReportRow(
                report_id=report_id,
                citizen_id=item.dataset_user_id,
                status="CLASIFICADO" if self.worker else "CREADO",
                created_at_utc_ns=created,
                media_hashes=(item.image_sha256,),
                created_rewards=((20, 20, "PENDIENTE"),),
                created_audit_rows=1,
                classified_audit_rows=1 if self.worker else 0,
                classification_models=(ex10.AI_MODEL_NAME,) if self.worker else (),
                status_history=(("", "CREADO"), ("CREADO", "CLASIFICADO"))
                if self.worker
                else (("", "CREADO"),),
            )
        task = {"request_id": item.request_id, "task_id": str(uuid.uuid4()),
                "task_name": ex10.TASK_NAME}  # fmt: skip
        self._event(run_id, {**task, "event": "celery.queued", "enqueue_at": iso(created + US),
                             "status": "queued", "db_transaction_open": False,
                             "process": process(API)})  # fmt: skip
        if not self.worker:
            return
        self._event(run_id, {**task, "event": "celery.started", "start_at": iso(created + 2 * US),
                             "status": "started", "process": process(WORKER)})  # fmt: skip
        if self.retry:
            retry = {"event": "celery.finished", "start_at": iso(created + 2 * US),
                     "finish_at": iso(created + 3 * US), "status": "finished",
                     "celery_state": "retry", "process": process(WORKER)}  # fmt: skip
            self._event(run_id, {**task, **retry})
            return
        self._event(run_id, {**task, "event": "celery.finished", "start_at": iso(created + 2 * US),
                             "finish_at": iso(created + 3 * US), "status": "finished",
                             "celery_state": "success", "process": process(WORKER)})  # fmt: skip

    def database(self) -> rec.DatabaseSnapshot:
        with self.lock:
            return rec.DatabaseSnapshot(dict(self.rows))


# --- Sondas y arnés del executor -----------------------------------------------------------------


@dataclass
class Harness:
    store: EvidenceStore
    plan: Any
    dataset: Any
    backend: Backend
    probes: executor.ExecutorProbes
    clock: FakeClock
    tokens: list[FakeTokens]
    run_ids: list[str] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.run_ids[-1]

    def start(self, **kwargs):
        options = {"git_sha": SHA, "source_root": Path("/nonexistent-source"),
                   "nonce": f"nonce-{uuid.uuid4().hex[:12]}",
                   "evidence_mode": executor.LOCAL_TEST_MODE, "timezone": "UTC"}  # fmt: skip
        options.update(kwargs)
        result = executor.start_run(self.store, probes=self.probes, **options)
        self.run_ids.append(result["run_id"])
        return result

    def execute(self, run_id=None):
        return executor.execute_run(
            self.store, run_id=run_id or self.run_id, git_sha=SHA, probes=self.probes
        )

    def drain(self, run_id=None):
        return executor.run_drain_step(
            self.store, run_id=run_id or self.run_id, git_sha=SHA, probes=self.probes
        )

    def reconcile(self, run_id=None):
        return executor.reconcile_run(
            self.store, run_id=run_id or self.run_id, git_sha=SHA, probes=self.probes
        )

    def finalize(self, run_id=None, **kwargs):
        return executor.finalize_run(
            self.store, run_id=run_id or self.run_id, git_sha=SHA, probes=self.probes, **kwargs
        )

    def state(self, run_id=None):
        return executor.load_state(self.store, run_id or self.run_id)

    def phase(self, run_id=None):
        return executor.phase_of(self.state(run_id))

    def payload(self, artifact_type, run_id=None):
        return self.state(run_id).payload(artifact_type)

    def secret_values(self) -> tuple[str, ...]:
        return tuple(value for tokens in self.tokens for value in tokens.values)

    def set_probes(self, **changes):
        self.probes = replace(self.probes, **changes)


def make_probes(backend: Backend, dataset, clock: FakeClock, tokens: list, **changes):
    aliases = [user["alias"] for user in dataset.payload["users"]]

    def issue(result):
        tokens.append(FakeTokens(aliases))
        return tokens[-1]

    probes = executor.ExecutorProbes(
        preflight=lambda options: preflight_report(git_sha=options.expected_git_sha),
        provider=lambda: [],
        sources=lambda root: [],
        fingerprint=lambda: "f" * 64,
        config=expected_config,
        ready_state=lambda data: {"state": "READY_STATE", "overall": "PASS"},
        fixture_result=lambda specs: object(),
        issue_tokens=issue,
        verify_tokens=lambda issued, result, now: tuple(TokenOk() for _ in range(10)),
        category_ids=lambda slugs: {s: str(uuid.uuid5(uuid.NAMESPACE_URL, s)) for s in slugs},
        resolve_backend=lambda: backend.address,
        database=backend.database,
        broker_dynamic=lambda: 0,
        monotonic_ns=clock,
        sleep=clock.sleep,
        boot_id=lambda: BOOT_ID,
        dataset=lambda: dataset,
    )
    return replace(probes, **changes)


def make_harness(name: str, dataset, **changes) -> Harness:
    store = make_store(name)
    plan = ex10.build_plan(dataset)
    backend = Backend(store, plan)
    clock = FakeClock()
    tokens: list[FakeTokens] = []
    probes = make_probes(backend, dataset, clock, tokens, **changes)
    return Harness(store, plan, dataset, backend, probes, clock, tokens)


def close_harness(harness: Harness) -> None:
    harness.backend.close()
    remove_store(harness.store)


def run_files(store: EvidenceStore, run_id: str) -> dict[str, bytes]:
    root = store.root / "runs" / run_id
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
