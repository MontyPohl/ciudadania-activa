"""Creación y envío de notificaciones por email (RF-48, §14 del plan).

Dos responsabilidades **deliberadamente separadas**, porque ocurren en momentos y
procesos distintos:

- `create()` — **síncrono**, dentro de la transacción de la acción que la origina. Solo
  escribe la fila en `notifications`. No habla con ningún servidor de correo, así que no
  puede hacer fallar ni ralentizar la operación de negocio.
- `send()` — **asíncrono**, desde la tarea de Celery en el worker. Es el único punto que
  habla con el proveedor de correo y el único que cambia el estado a ENVIADO o FALLIDO.
- `resend()` — **manual y explícito**, desde el comando `resend_notification`. Devuelve
  una notificación FALLIDA a PENDIENTE y vuelve a encolar su envío. No es un reintento
  automático: lo dispara una persona, caso por caso, y queda auditado.

Esa separación es lo que hace que una revisión de identidad no dependa de que el correo
salga: la decisión administrativa queda registrada aunque el proveedor de correo esté
caído, y el fallo se ve en el estado de la notificación.

El Sprint 4 incorpora la verificación de identidad y el Sprint 12 agrega el canje
simulado, siempre en texto plano. Las otras plantillas y los reintentos automáticos
quedan para el Sprint 13 (CT-28).
"""

from __future__ import annotations

import logging
from functools import partial

from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.services import audit
from apps.notifications_app.enums import NotificationChannel, NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import brevo

logger = logging.getLogger(__name__)

#: Acción de auditoría del reintento manual (§30 del plan). Sigue la convención del
#: proyecto —`identity.submitted`, `identity.approved`, `user.role_promoted`—: dominio
#: en singular, verbo en pasado.
ACCION_REENVIO = "notification.resent"


class ResendError(Exception):
    """La notificación no admite un reenvío manual. El comando lo traduce a `CommandError`."""


#: Asunto y plantilla de cada tipo que se sabe enviar. Un tipo que no esté
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
    NotificationType.CANJE_REALIZADO: (
        "Registramos tu canje simulado",
        "notifications/canje_realizado.txt",
    ),
}


def create(user, notification_type: str, *, redemption=None) -> Notification:
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
        redemption=redemption,
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

    Consecuencia asumida: **el lock de la fila dura lo que tarde en responder Brevo**.
    Es aceptable en este MVP —el volumen es de unos pocos correos de verificación, y el
    cliente impone un timeout explícito de 15 s— y la alternativa sería añadir un estado
    intermedio `ENVIANDO` al enumerado, es decir, cambiar el esquema para optimizar algo
    que todavía no duele.

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
            # Del error se registra **solo el tipo**, nunca su mensaje: un rechazo del
            # proveedor suele incluir la dirección del destinatario. El id de la
            # notificación basta para investigar el caso en el admin.
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


def resend(notification_id) -> Notification:
    """Devuelve una notificación FALLIDA a PENDIENTE y vuelve a encolar su envío.

    Es el **reintento manual** del §14 del plan: lo dispara una persona con
    `resend_notification`, caso por caso, y queda en `audit_logs`. No es —ni se
    convierte en— un reintento automático: `send()` sigue tratando FALLIDO como terminal
    y la tarea de Celery sigue sin `autoretry_for`. Los reintentos automáticos son
    alcance de los Sprints 12-13.

    El estado se relee **con la fila ya bloqueada**, igual que en `send()`: dos
    operadores que ejecuten el comando a la vez no pueden reactivar la misma
    notificación dos veces ni encolar dos tareas. El primero la pasa a PENDIENTE; el
    segundo, al tomar el lock, ya la ve PENDIENTE y recibe `ResendError`.

    El encolado va en `on_commit` y no dentro de la transacción: si esta se deshace, no
    debe quedar una tarea persiguiendo un cambio de estado que nunca se guardó. Esa es
    también la razón de que no haya carrera entre el cambio de estado y el encolado —el
    worker no puede empezar a trabajar sobre una fila que todavía no está confirmada—.

    No habla con Brevo. El envío real lo hace `send()` desde el worker, con el mismo
    camino que cualquier otra notificación.
    """
    with transaction.atomic():
        notificacion = (
            Notification.objects.select_for_update()
            .select_related("user")
            .filter(pk=notification_id)
            .first()
        )

        if notificacion is None:
            raise Notification.DoesNotExist("No existe esa notificación.")

        if notificacion.status != NotificationStatus.FALLIDO:
            # PENDIENTE ya está en camino y ENVIADO ya salió: reactivar cualquiera de
            # los dos duplicaría un correo. Se rechaza sin escribir nada.
            raise ResendError(
                f"Solo se puede reenviar una notificación en FALLIDO; "
                f"esta está en {notificacion.status}."
            )

        notificacion.status = NotificationStatus.PENDIENTE
        notificacion.save(update_fields=["status", "updated_at"])

        audit.log(None, ACCION_REENVIO, _payload_reenvio(notificacion))

        transaction.on_commit(partial(_encolar_reenvio, notificacion.pk))

    return notificacion


def _payload_reenvio(notificacion: Notification) -> dict[str, str]:
    """Solo identificadores y el tipo. Nunca el destinatario ni el cuerpo del correo.

    El email del ciudadano no aporta nada aquí: `target_user_id` ya identifica a quién
    se le reenvía, y §30 no pide más. Tampoco entra el motivo del fallo anterior, que
    podía traer la dirección en el mensaje del proveedor.
    """
    return {
        "notification_id": str(notificacion.pk),
        "notification_type": notificacion.notification_type,
        "target_user_id": str(notificacion.user_id),
    }


def _encolar_reenvio(notification_id) -> None:
    """Encola el envío tras el commit. Si el broker no responde, deshace la reactivación.

    Importación diferida: `tasks` importa este módulo, así que hacerlo en tiempo de
    módulo cerraría el ciclo.

    Un fallo al encolar dejaría la notificación en PENDIENTE **sin nadie que la
    procese**, y el comando ya no podría volver a reactivarla porque exige FALLIDO. Así
    que se devuelve a FALLIDO: el operador arregla el broker y vuelve a ejecutar el
    mismo comando. La entrada de auditoría del intento **se conserva** —alguien lo
    intentó, y eso ocurrió—.
    """
    from apps.notifications_app.tasks import send_notification

    try:
        send_notification.delay(notification_id)
    except Exception as exc:
        logger.warning(
            "No se pudo encolar el reenvío de la notificación %s (%s).",
            notification_id,
            type(exc).__name__,
        )
        Notification.objects.filter(pk=notification_id).update(
            status=NotificationStatus.FALLIDO, updated_at=timezone.now()
        )


def _enviar_email(notificacion: Notification) -> None:
    """Compone y entrega el correo. Texto plano, sin adjuntos y sin datos sensibles.

    El cuerpo **no** lleva la cédula, el HMAC, la selfie, su clave de objeto, ninguna
    URL firmada ni el motivo libre del rechazo: para eso está la plataforma, que exige
    autenticación. Un correo viaja por servidores de terceros y queda en un buzón.
    """
    asunto, plantilla = PLANTILLAS[notificacion.notification_type]
    destinatario = notificacion.user.email
    cuerpo = render_to_string(plantilla, _contexto(notificacion))

    brevo.send_email(to=destinatario, subject=asunto, text_content=cuerpo)


def _contexto(notificacion: Notification) -> dict[str, str]:
    """Mantiene el contexto de S4 y agrega los nombres del canje, nunca su código."""
    contexto = {"nombre": notificacion.user.full_name or notificacion.user.email.split("@")[0]}
    if notificacion.notification_type == NotificationType.CANJE_REALIZADO:
        if notificacion.redemption_id is None:
            raise ValueError("Un aviso de canje requiere su vale.")
        redemption = notificacion.redemption
        contexto.update(
            institucion=redemption.institution.name,
            recompensa=redemption.reward.name,
        )
    return contexto
