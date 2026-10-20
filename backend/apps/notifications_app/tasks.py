"""Tareas de Celery de notificaciones (Sprint 4).

Una sola, y deliberadamente delgada: la lógica vive en `services.dispatch`, igual que en
el resto del proyecto las reglas viven en `services/` y no en la capa de transporte. La
tarea es transporte; que el transporte sea una cola en vez de HTTP no cambia la regla.

**Sin reintentos de Celery**: ni `autoretry_for`, ni `retry_backoff`, ni `max_retries`.
Desde el Sprint 13 los reintentos automáticos viven en la base: `dispatch.send` deja una
PENDIENTE con `next_attempt_at` ante un fallo transitorio, y `dispatch_due`, agendada en
Beat, la vuelve a encolar. El estado vive en PostgreSQL, no en el broker. Una
notificación que agota sus intentos, o que falla de forma permanente, queda FALLIDO:
un estado consultable en el admin, no un mensaje perdido en silencio.
"""

from celery import shared_task

from apps.notifications_app.services import dispatch


@shared_task(name="notifications.send_notification")
def send_notification(notification_id) -> None:
    """Entrega una notificación ya registrada.

    Es **idempotente**: `dispatch.send` bloquea la fila y relee su estado, de modo que
    dos entregas simultáneas del mismo mensaje —algo que Celery no garantiza que no
    ocurra— producen un solo correo.

    No propaga la excepción de un envío fallido: el resultado queda escrito en el estado
    de la notificación. Relanzarla solo marcaría la tarea como fallida en el broker sin
    aportar nada, porque no hay reintentos que disparar.
    """
    dispatch.send(notification_id)


@shared_task(name="notifications.dispatch_due")
def dispatch_due() -> int:
    """Barrido de Beat (cada 5 min): reencola los reintentos vencidos (Sprint 13)."""
    return dispatch.dispatch_due()
