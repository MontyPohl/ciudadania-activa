"""Sustituciones acotadas a una ejecución de management command, sin proveedores."""

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from .isolation import DatasetError


class Clock:
    def __init__(self, instant):
        self.instant = instant

    def now(self):
        return self.instant


@contextmanager
def local_runtime(objects, clock):
    from celery.app.task import Task

    from apps.core.services import storage

    captured = []

    def enqueue(task, args=None, kwargs=None, **options):
        captured.append(
            {
                "task": task.name,
                "args": [str(a) for a in args or []],
                "kwargs": {k: str(v) for k, v in (kwargs or {}).items()},
                "captured_at": clock.now().isoformat(),
                "delivered": False,
            }
        )
        return SimpleNamespace(id=f"s15-captured-{len(captured)}")

    def network_blocked(*args, **kwargs):
        raise DatasetError("La herramienta S15 bloquea llamadas HTTP y envíos externos.")

    def invalidate(*args, **kwargs):
        return None

    with ExitStack() as stack:
        for attribute, replacement in {
            "upload": objects.upload,
            "download_private": objects.download,
            "delete": objects.delete,
            "create_signed_url": objects.signed_url,
            "ensure_configured": lambda: None,
        }.items():
            stack.enter_context(patch.object(storage, attribute, replacement))
        stack.enter_context(patch("django.utils.timezone.now", clock.now))
        stack.enter_context(patch.object(Task, "apply_async", enqueue))
        stack.enter_context(patch("celery.app.base.Celery.send_task", network_blocked))
        stack.enter_context(patch("requests.sessions.Session.request", network_blocked))
        for name in (
            "apps.map_app.cache",
            "apps.reports_app.services.reports",
            "apps.reports_app.services.validation",
            "apps.reports_app.services.lifecycle",
            "apps.reports_app.services.reversal",
            "apps.reports_app.services.auto_close",
            "apps.social_ingestion_app.services.x_mock",
        ):
            module = __import__(name, fromlist=["invalidate_public_reports_cache"])
            if hasattr(module, "invalidate_public_reports_cache"):
                stack.enter_context(
                    patch.object(module, "invalidate_public_reports_cache", invalidate)
                )
        yield captured
