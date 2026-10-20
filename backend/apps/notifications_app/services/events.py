"""Avisos por correo de los eventos de un reporte (Sprint 13, S13-D09).

Los servicios de `reports_app` llaman a `notify_report_event` **dentro** de su
transacción, igual que identidad (S4) y canje (S12) llaman a `dispatch.create`: si la
decisión se revierte, el aviso se va con ella. El envío ocurre después del commit.

Solo se notifica al autor del reporte, y solo por los eventos que deciden personas:
creación, validación, rechazo y resolución. No hay correo por `EN_PROCESO`, por el
cierre, por la reversión, por las confirmaciones ni por la clasificación de la IA.
"""

from apps.notifications_app.enums import NotificationType
from apps.notifications_app.services import dispatch


def notify_report_event(report, notification_type, *, institution=None, cycle=None):
    """Registra el aviso del evento para el autor y encola su envío tras el commit.

    Devuelve la notificación creada, o `None` si no corresponde: un reporte sin autor
    —los de X simulado, que no acreditan ni notifican a su fuente (RF-45)—, un autor
    inactivo, o un evento que ya tenía su aviso. La clave de idempotencia lleva el ciclo
    de resolución cuando el evento puede repetirse tras una reversión y revalidación.
    """
    # Los servicios de reportes pasan el valor como texto para no importar este módulo a
    # nivel de módulo; convertirlo acá hace que un valor mal escrito falle en el acto.
    notification_type = NotificationType(notification_type)
    if report.citizen_id is None:
        return None
    citizen = report.citizen
    if not citizen.is_active:
        return None

    dedup_key = f"{notification_type.value}:{report.pk}"
    if cycle is not None:
        dedup_key = f"{dedup_key}:{cycle}"

    notification = dispatch.create(
        citizen,
        notification_type,
        report=report,
        institution=institution,
        dedup_key=dedup_key,
    )
    if notification is not None:
        dispatch.enqueue_on_commit(notification.pk)
    return notification
