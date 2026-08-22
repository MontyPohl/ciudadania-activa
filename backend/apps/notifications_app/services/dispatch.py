"""Creación y envío de notificaciones por email (RF-48, §14 del plan).

Dos responsabilidades **deliberadamente separadas**, porque ocurren en momentos y
procesos distintos:

- `create()` — **síncrono**, dentro de la transacción de la acción que la origina. Solo
  escribe la fila en `notifications`. No habla con ningún servidor de correo, así que no
  puede hacer fallar ni ralentizar la operación de negocio.
- `send()` — **asíncrono**, desde la tarea de Celery en el worker. Es el único punto que
  toca SMTP y el único que cambia el estado a ENVIADO o FALLIDO.

Esa separación es lo que hace que una revisión de identidad no dependa de que el correo
salga: la decisión administrativa queda registrada aunque el proveedor de correo esté
caído, y el fallo se ve en el estado de la notificación.

Alcance del Sprint 4: las dos plantillas de verificación de identidad, en texto plano.
Las seis restantes, las plantillas HTML y los reintentos automáticos llegan en los
Sprints 12 y 13 (CT-28).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string

from apps.notifications_app.enums import NotificationChannel, NotificationStatus, NotificationType
from apps.notifications_app.models import Notification

logger = logging.getLogger(__name__)

#: Asunto y plantilla de cada tipo que el Sprint 4 sabe enviar. Un tipo que no esté
#: aquí no se envía: es preferible dejarlo FALLIDO y visible a inventar un correo.
PLANTILLAS = {
    NotificationType.VERIFICACION_APROBADA: (
        "Tu identidad fue verificada",
        "notifications/verificacion_aprobada.txt",
    ),
    NotificationType.VERIFICACION_RECHAZADA: (
        "Sobre tu verificación de identidad",
        "notifications/verificacion_rechazada.txt",
    ),
}


def create(user, notification_type: str) -> Notification:
    """Registra la notificación como PENDIENTE. **No envía nada.**

    Se llama dentro de la transacción de la acción que la origina, de modo que un
    rollback se lleva también la fila: no queda constancia de un aviso por una decisión
    que nunca se confirmó.

    `report` queda a `NULL`: los correos de verificación de identidad no se refieren a
    ningún reporte, y el modelo lo previó así desde el Sprint 2.
    """
    return Notification.objects.create(
        user=user,
        report=None,
        notification_type=notification_type,
        channel=NotificationChannel.EMAIL,
        status=NotificationStatus.PENDIENTE,
    )


def send(notification_id) -> Notification | None:
    """Envía la notificación y deja su estado en ENVIADO o FALLIDO.

    **Idempotente y a prueba de concurrencia.** La fila se bloquea con
    `select_for_update()` y su estado se relee ya con el lock tomado: si otra ejecución
    se adelantó, esta se retira sin enviar. Sin ese bloqueo, dos entregas simultáneas
    del mismo mensaje —algo perfectamente posible en Celery— producirían dos correos.

    Consecuencia asumida: **el lock de la fila dura lo que tarde en responder el
    servidor SMTP**. Es aceptable en este MVP —el volumen es de unos pocos correos de
    verificación— y la alternativa sería añadir un estado intermedio `ENVIANDO` al
    enumerado, es decir, cambiar el esquema para optimizar algo que todavía no duele.

    Sin reintentos: los reintentos automáticos son alcance de los Sprints 12-13. Una
    notificación FALLIDA queda registrada como tal y es visible en el admin.
    """
    with transaction.atomic():
        notificacion = (
            Notification.objects.select_for_update()
            .select_related("user")
            .filter(pk=notification_id)
            .first()
        )

        if notificacion is None:
            logger.warning("No existe la notificación %s que se pidió enviar.", notification_id)
            return None

        if notificacion.status != NotificationStatus.PENDIENTE:
            # Ya la envió —o la falló— otra ejecución. Reenviar sería duplicar.
            return notificacion

        try:
            _enviar_email(notificacion)
        except Exception as exc:
            # Se captura de forma amplia y a propósito: cualquier fallo del correo debe
            # traducirse a FALLIDO. Dejar escapar la excepción dejaría la fila en
            # PENDIENTE para siempre, que es justo el estado que no debe quedar.
            #
            # Del error se registra **solo el tipo**, nunca su mensaje: un rechazo de
            # SMTP suele incluir la dirección del destinatario. El id de la notificación
            # basta para investigar el caso en el admin.
            notificacion.status = NotificationStatus.FALLIDO
            notificacion.save(update_fields=["status", "updated_at"])
            logger.warning(
                "Falló el envío de la notificación %s (%s).",
                notificacion.pk,
                type(exc).__name__,
            )
        else:
            notificacion.status = NotificationStatus.ENVIADO
            notificacion.save(update_fields=["status", "updated_at"])

    return notificacion


def _enviar_email(notificacion: Notification) -> None:
    """Compone y entrega el correo. Texto plano, sin adjuntos y sin datos sensibles.

    El cuerpo **no** lleva la cédula, el HMAC, la selfie, su clave de objeto, ninguna
    URL firmada ni el motivo libre del rechazo: para eso está la plataforma, que exige
    autenticación. Un correo viaja por servidores de terceros y queda en un buzón.
    """
    asunto, plantilla = PLANTILLAS[notificacion.notification_type]
    destinatario = notificacion.user.email
    nombre = notificacion.user.full_name or destinatario.split("@")[0]

    cuerpo = render_to_string(plantilla, {"nombre": nombre})

    send_mail(
        subject=asunto,
        message=cuerpo,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[destinatario],
        fail_silently=False,
    )
