"""Observador opt-in del worker S15; no instrumenta el runtime del producto."""

import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from celery.signals import task_postrun, task_prerun

from tools.s15.local_profile import validate_environment

_starts = {}
_lock = threading.Lock()
_allowed = {"ai.classify_report", "notifications.send_notification"}


def _target(args, kwargs):
    value = args[0] if args else next(iter((kwargs or {}).values()), None)
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


@task_prerun.connect(weak=False)
def started(sender=None, task_id=None, **kwargs):
    if sender is not None and sender.name in _allowed:
        with _lock:
            _starts[task_id] = time.monotonic()


@task_postrun.connect(weak=False)
def finished(sender=None, task_id=None, args=None, kwargs=None, state=None, **unused):
    if sender is None or sender.name not in _allowed:
        return
    validate_environment(os.environ)
    output = Path(os.environ.get("S15_TASK_RECEIPTS", ""))
    if (
        not output.is_absolute()
        or not output.is_relative_to(Path("/out/s15"))
        or output.resolve() != output
        or any(p.is_symlink() for p in (output, *output.parents))
        or not output.parent.is_dir()
        or output.parent.stat().st_mode & 0o077
    ):
        raise ValueError("Recibos de tareas requieren destino privado S15.")
    target = _target(args, kwargs)
    if target is None:
        raise ValueError("La tarea observada no identifica una entidad UUID.")
    with _lock:
        start = _starts.pop(task_id, None)
        record = {
            "taskId": str(uuid.UUID(str(task_id))),
            "name": sender.name,
            "targetId": target,
            "state": state if state in {"SUCCESS", "FAILURE", "RETRY"} else "UNKNOWN",
            "finishedAt": datetime.now(UTC).isoformat(),
            "elapsedMs": None if start is None else (time.monotonic() - start) * 1000,
            "scope": "local worker; IA disabled; mail substituted",
        }
        descriptor = os.open(output, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(descriptor).st_mode & 0o077:
                raise ValueError("Recibos de tareas con permisos inseguros.")
            os.write(descriptor, (json.dumps(record) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
