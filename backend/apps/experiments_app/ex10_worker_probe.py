"""Probe de control del worker Celery para el preflight de EX-10 (fase 2B-2).

Es un comando de inspección de Celery (`Panel`), no una tarea: corre dentro del proceso
worker que atiende el pidbox y responde por la cola de respuesta del control plane. Nunca
publica tareas, nunca clasifica y nunca toca la base.

La respuesta es una whitelist cerrada: identidad del nodo y del proceso, pool, `eager`,
broker y result backend reducidos a esquema, host lógico y base, fake de IA, adapter
efectivo, registro de `ai.classify_report` y versiones. Nunca devuelve URLs, entorno,
settings, rutas ni mensajes de excepción: ante cualquier error responde un código.

Importar este módulo no registra nada. El registro lo hace `register()`, que llama
`apps.experiments_app.tasks` cuando el worker autodescubre los módulos `tasks`.
"""

from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import celery
import kombu
from celery.worker.control import Panel, inspect_command
from django.conf import settings

from . import ex10_load

PROBE_COMMAND = "s11_ex10_worker_probe"
PROBE_SCHEMA_VERSION = "s11-ex10-worker-probe-v1"
#: Nonce del preflight: 32 hex. Cualquier otro valor no se refleja en la respuesta.
NONCE_FORMAT = re.compile(r"^[0-9a-f]{32}$")
NODE_FORMAT = re.compile(r"^[A-Za-z0-9_.-]{1,64}@[A-Za-z0-9_.-]{1,253}$")
HOSTNAME_FORMAT = re.compile(r"^[A-Za-z0-9_.-]{1,253}$")
TOKEN_FORMAT = re.compile(r"^[A-Za-z0-9_.:+-]{1,96}$")
VERSION_FORMAT = re.compile(r"^[0-9]{1,4}(?:\.[0-9A-Za-z]{1,8}){1,4}$")
#: Fragmentos que nunca pueden aparecer en un valor de la respuesta.
FORBIDDEN_FRAGMENTS = (
    "://",
    "bearer",
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "eyj",
    "sk-",
    "api_key",
    "apikey",
)

_ENDPOINT_FIELDS = frozenset({"scheme", "host", "db", "matches_protocol"})
RESPONSE_FIELDS = frozenset(
    {
        "schema_version",
        "nonce",
        "node",
        "process",
        "pool",
        "task_always_eager",
        "broker",
        "result_backend",
        "fake_ai_enabled",
        "ai_adapter",
        "classify_task_registered",
        "versions",
    }
)
ERROR_FIELDS = frozenset({"schema_version", "nonce", "error"})
_NOT_FAKE = "not-s11-fake"


# --- Hechos compartidos por el API y el worker ---------------------------------------------


def endpoint_facts(url: Any) -> dict[str, Any]:
    """Esquema, host lógico y base de una URL Redis; nunca credenciales ni la URL.

    `matches_protocol` compara con `BROKER_URL` de G0 esquema, host, puerto, base, y
    exige que no haya usuario, contraseña ni query.
    """
    invalid = {"scheme": "invalid", "host": "invalid", "db": None, "matches_protocol": False}
    if not isinstance(url, str) or not url:
        return invalid
    try:
        parts = urlsplit(url)
        db = int(parts.path.lstrip("/") or "0")
        identity = (parts.scheme, parts.hostname, parts.port, parts.path)
        credentials = (parts.username, parts.password, parts.query)
    except ValueError:
        return invalid
    protocol = urlsplit(ex10_load.BROKER_URL)
    expected = (protocol.scheme, protocol.hostname, protocol.port, protocol.path)
    scheme, host = parts.scheme, parts.hostname or ""
    return {
        "scheme": scheme if TOKEN_FORMAT.fullmatch(scheme) else "invalid",
        "host": host if HOSTNAME_FORMAT.fullmatch(host) else "invalid",
        "db": db,
        "matches_protocol": identity == expected and credentials == (None, None, ""),
    }


def effective_ai_adapter() -> str:
    """Adapter que usaría `ai.classify_report` en este proceso, sin construir proveedores."""
    from apps.ai_client_app import tasks as ai_tasks

    from .ai_runtime import get_experimental_runtime_adapter

    if ai_tasks.get_runtime_adapter is get_experimental_runtime_adapter:
        return get_experimental_runtime_adapter().model_name
    return _NOT_FAKE


def classify_task_registered(app) -> bool:
    from apps.ai_client_app import tasks as ai_tasks  # noqa: F401 - registra la tarea

    return ex10_load.TASK_NAME in app.tasks


# --- Comando del worker ---------------------------------------------------------------------


def probe_payload(state, nonce: Any) -> dict[str, Any]:
    """Respuesta del worker. Nunca lanza: un fallo es un código, sin mensaje original."""
    echoed = nonce if isinstance(nonce, str) and NONCE_FORMAT.fullmatch(nonce) else None
    try:
        app = state.app
        info = state.consumer.pool.info
        return {
            "schema_version": PROBE_SCHEMA_VERSION,
            "nonce": echoed,
            "node": str(state.hostname),
            "process": {"hostname": socket.gethostname(), "pid": os.getpid()},
            "pool": {
                "implementation": str(info.get("implementation", "")),
                "max_concurrency": info.get("max-concurrency"),
            },
            "task_always_eager": bool(app.conf.task_always_eager),
            "broker": endpoint_facts(app.conf.broker_url),
            "result_backend": endpoint_facts(app.conf.result_backend),
            "fake_ai_enabled": bool(getattr(settings, "S11_FAKE_AI_ENABLED", False)),
            "ai_adapter": effective_ai_adapter(),
            "classify_task_registered": classify_task_registered(app),
            "versions": {"celery": celery.__version__, "kombu": kombu.__version__},
        }
    except Exception:  # noqa: BLE001 - el pidbox respondería repr(exc) si esto lanzara
        return {"schema_version": PROBE_SCHEMA_VERSION, "nonce": echoed, "error": "probe-failed"}


def worker_probe(state, nonce=None, **kwargs):
    """Probe EX-10 de sólo lectura: identidad y configuración efectiva del worker."""
    return probe_payload(state, nonce)


worker_probe.__ex10_probe__ = True


def register() -> None:
    """Registro idempotente en `Panel`; se niega a pisar un comando ajeno con el mismo nombre."""
    current = Panel.data.get(PROBE_COMMAND)
    if current is not None:
        if getattr(current, "__ex10_probe__", False):
            return
        raise RuntimeError("Ya existe un comando de control ajeno con el nombre del probe EX-10.")
    inspect_command(name=PROBE_COMMAND, args=[("nonce", str)], signature="<nonce>")(worker_probe)


# --- Validación en el preflight --------------------------------------------------------------


class ReplyRejected(ValueError):
    """Respuesta del worker fuera de la whitelist. `code` nunca lleva el valor recibido."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _safe_text(value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ReplyRejected("worker-probe-field-format")
    lowered = value.lower()
    if any(fragment in lowered for fragment in FORBIDDEN_FRAGMENTS):
        raise ReplyRejected("worker-probe-possible-secret")
    return value


def _exact(mapping: Any, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(mapping, Mapping) or set(mapping) != fields:
        raise ReplyRejected("worker-probe-unexpected-fields")
    return mapping


def _int(value: Any) -> int:
    if type(value) is not int:
        raise ReplyRejected("worker-probe-field-type")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise ReplyRejected("worker-probe-field-type")
    return value


def _endpoint(value: Any) -> dict[str, Any]:
    endpoint = _exact(value, _ENDPOINT_FIELDS)
    db = endpoint["db"]
    if db is not None:
        _int(db)
    return {
        "scheme": _safe_text(endpoint["scheme"], TOKEN_FORMAT),
        "host": _safe_text(endpoint["host"], HOSTNAME_FORMAT),
        "db": db,
        "matches_protocol": _bool(endpoint["matches_protocol"]),
    }


def validate_reply(reply: Any, nonce: str) -> dict[str, Any]:
    """Una respuesta `{nodo: payload}` de `broadcast`, validada y normalizada."""
    if not isinstance(reply, Mapping) or len(reply) != 1:
        raise ReplyRejected("worker-probe-reply-shape")
    ((node, payload),) = reply.items()
    node = _safe_text(node, NODE_FORMAT)
    if isinstance(payload, Mapping) and set(payload) == {"error"}:
        # El pidbox responde así cuando el comando no existe en el worker: otro código.
        raise ReplyRejected("worker-probe-not-registered")
    if isinstance(payload, Mapping) and set(payload) == ERROR_FIELDS:
        if payload.get("schema_version") == PROBE_SCHEMA_VERSION:
            raise ReplyRejected("worker-probe-internal-error")
    fields = _exact(payload, RESPONSE_FIELDS)
    if fields["schema_version"] != PROBE_SCHEMA_VERSION:
        raise ReplyRejected("worker-probe-schema-version")
    if fields["nonce"] != nonce:
        raise ReplyRejected("worker-probe-nonce-mismatch")
    if fields["node"] != node:
        raise ReplyRejected("worker-probe-node-mismatch")
    process = _exact(fields["process"], frozenset({"hostname", "pid"}))
    pool = _exact(fields["pool"], frozenset({"implementation", "max_concurrency"}))
    versions = _exact(fields["versions"], frozenset({"celery", "kombu"}))
    pid = _int(process["pid"])
    if pid <= 0:
        raise ReplyRejected("worker-probe-field-type")
    return {
        "node": node,
        "process": {"hostname": _safe_text(process["hostname"], HOSTNAME_FORMAT), "pid": pid},
        "pool": {
            "implementation": _safe_text(pool["implementation"], TOKEN_FORMAT),
            "max_concurrency": _int(pool["max_concurrency"]),
        },
        "task_always_eager": _bool(fields["task_always_eager"]),
        "broker": _endpoint(fields["broker"]),
        "result_backend": _endpoint(fields["result_backend"]),
        "fake_ai_enabled": _bool(fields["fake_ai_enabled"]),
        "ai_adapter": _safe_text(fields["ai_adapter"], TOKEN_FORMAT),
        "classify_task_registered": _bool(fields["classify_task_registered"]),
        "versions": {
            "celery": _safe_text(versions["celery"], VERSION_FORMAT),
            "kombu": _safe_text(versions["kombu"], VERSION_FORMAT),
        },
    }
