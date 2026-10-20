"""Tareas de Celery de recompensas (Sprint 13).

Delgada, como `notifications.send_notification`: la regla vive en
`services.availability`. Sin reintentos de Celery: el lote es idempotente por la
`dedup_key` única y Beat lo vuelve a correr al día siguiente.
"""

from celery import shared_task

from apps.rewards_app.services import availability


@shared_task(name="rewards.notify_available_rewards")
def notify_available_rewards() -> int:
    """Lote diario de Beat (09:00 locales): avisos de recompensa disponible."""
    return availability.notify_available_rewards()
