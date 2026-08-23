"""Tareas de Celery de notificaciones (Sprint 4).

Una sola, y deliberadamente delgada: la lógica vive en `services.dispatch`, igual que en
el resto del proyecto las reglas viven en `services/` y no en la capa de transporte. La
tarea es transporte; que el transporte sea una cola en vez de HTTP no cambia la regla.

**Sin reintentos automáticos**: ni `autoretry_for`, ni `retry_backoff`, ni `max_retries`.
El §14 del plan los sitúa en los Sprints 12-13. Una notificación que falla queda en
FALLIDO, que es un estado consultable en el admin, no un mensaje perdido en silencio.

**Sin Celery Beat**: esta tarea la dispara un evento —la revisión de una identidad—, no
un reloj.
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
