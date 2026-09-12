"""Barrido periódico de autocierre de reportes."""

import logging
import random

from celery import shared_task
from django.db import OperationalError

from apps.reports_app.services import auto_close

logger = logging.getLogger(__name__)

AUTO_CLOSE_MAX_RETRIES = 3
BACKOFF_BASE = 2


def compute_retry_countdown(retries: int) -> float:
    base = BACKOFF_BASE**retries
    return base + random.uniform(0, base)


@shared_task(bind=True, name="reports.auto_close", max_retries=AUTO_CLOSE_MAX_RETRIES)
def auto_close_reports(self):
    """Cierra candidatos elegibles; una entrega posterior solo observa no-ops."""
    try:
        report_ids = auto_close.eligible_report_ids()
    except OperationalError as exc:
        return _retry_transient(self, exc)
    except Exception as exc:  # noqa: BLE001 - se registra solo el tipo, sin payload sensible.
        _log_permanent_failure(exc)
        return 0

    closed = 0
    for report_id in report_ids:
        try:
            result = auto_close.close_report(report_id=report_id)
        except OperationalError as exc:
            return _retry_transient(self, exc)
        except Exception as exc:  # noqa: BLE001 - el próximo barrido conserva elegibilidad.
            _log_permanent_failure(exc, report_id=report_id)
            continue
        closed += int(result.changed)
    return closed


def _retry_transient(task, exc):
    if task.request.retries >= AUTO_CLOSE_MAX_RETRIES:
        logger.warning(
            "Autocierre agotado tras %s reintentos por un error transitorio.",
            AUTO_CLOSE_MAX_RETRIES,
        )
        return 0
    raise task.retry(
        exc=exc,
        countdown=compute_retry_countdown(task.request.retries),
        max_retries=AUTO_CLOSE_MAX_RETRIES,
    ) from exc


def _log_permanent_failure(exc, *, report_id=None):
    logger.error(
        "Autocierre omitido por error permanente tipo=%s report_id=%s.",
        type(exc).__name__,
        report_id or "unknown",
    )
