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
simulado, siempre en texto plano. El Sprint 13 completa los ocho tipos (eventos de
reporte y recompensa disponible), agrega la idempotencia por `dedup_key` y los
reintentos automáticos acotados: solo ante fallos transitorios y solo mientras la
notificación sigue PENDIENTE (S13-D11). FALLIDO sigue siendo terminal (CT-51).
"""

from __future__ import annotations

import logging
from functools import partial

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.services import audit
from apps.notifications_app import retry
from apps.notifications_app.enums import NotificationChannel, NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import brevo

logger = logging.getLogger(__name__)

#: Acción de auditoría del reintento manual (§30 del plan). Sigue la convención del
#: proyecto —`identity.submitted`, `identity.approved`, `user.role_promoted`—: dominio
#: en singular, verbo en pasado.
ACCION_REENVIO = "notification.resent"

#: Nombre de la constraint de idempotencia (`Notification.Meta.constraints`).
DEDUP_CONSTRAINT = "notification_dedup_key_unica"

#: Tipos de evento de reporte (Sprint 13): sus plantillas nombran fecha y, salvo la de
#: creación, categoría e institución.
TIPOS_DE_REPORTE = frozenset(
    {
        NotificationType.REPORTE_CREADO,
        NotificationType.VALIDACION_INSTITUCIONAL,
        NotificationType.REPORTE_RESUELTO,
        NotificationType.REPORTE_RECHAZADO,
    }
)


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
    NotificationType.REPORTE_CREADO: (
        "Recibimos tu reporte",
        "notifications/reporte_creado.txt",
    ),
    NotificationType.VALIDACION_INSTITUCIONAL: (
        "Tu reporte fue validado",
        "notifications/validacion_institucional.txt",
    ),
    NotificationType.REPORTE_RESUELTO: (
        "Tu reporte figura como resuelto",
        "notifications/reporte_resuelto.txt",
    ),
    NotificationType.REPORTE_RECHAZADO: (
        "Sobre tu reporte",
        "notifications/reporte_rechazado.txt",
    ),
    NotificationType.RECOMPENSA_DISPONIBLE: (
        "Tenés una recompensa simulada disponible",
        "notifications/recompensa_disponible.txt",
    ),
}


def create(
    user,
    notification_type: str,
    *,
    report=None,
    redemption=None,
    institution=None,
    dedup_key: str | None = None,
) -> Notification | None:
    """Registra la notificación como PENDIENTE. **No envía nada.**

    Se llama dentro de la transacción de la acción que la origina, de modo que un
    rollback se lleva también la fila: no queda constancia de un aviso por una decisión
    que nunca se confirmó.

    Sin `dedup_key` se comporta como en S4 y S12 y siempre devuelve la fila. Con
    `dedup_key` (Sprint 13) la inserción va en un *savepoint*: si la clave ya existe,
    devuelve `None` sin abortar la transacción del llamador, porque ese evento ya tiene
    su aviso. Cualquier otro `IntegrityError` se relanza.
    """
    campos = {
        "user": user,
        "report": report,
        "redemption": redemption,
        "institution": institution,
        "notification_type": notification_type,
        "channel": NotificationChannel.EMAIL,
        "status": NotificationStatus.PENDIENTE,
    }
    if dedup_key is None:
        return Notification.objects.create(**campos)

    try:
        with transaction.atomic():
            return Notification.objects.create(dedup_key=dedup_key, **campos)
    except IntegrityError as exc:
        if _constraint_name(exc) != DEDUP_CONSTRAINT:
            raise
        return None


def enqueue_on_commit(notification_id) -> None:
    """Encola el envío cuando la transacción del llamador se confirma.

    Si el broker no responde, la notificación queda FALLIDO, igual que en los
    encoladores de identidad (S4) y canje (S12): visible y recuperable con
    `resend_notification`. Nunca deja escapar la excepción hacia la operación de negocio.
    """
    transaction.on_commit(partial(_encolar, notification_id), robust=True)


def _encolar(notification_id) -> None:
    from apps.notifications_app.tasks import send_notification

    try:
        send_notification.delay(notification_id)
    except Exception as exc:
        logger.warning(
            "No se pudo encolar el envío de la notificación %s (%s).",
            notification_id,
            type(exc).__name__,
        )
        Notification.objects.filter(pk=notification_id).update(
            status=NotificationStatus.FALLIDO, updated_at=timezone.now()
        )


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)


def send(notification_id, *, now=None) -> Notification | None:
    """Envía la notificación y deja su estado en ENVIADO, FALLIDO o PENDIENTE reprogramada.

    **Idempotente y a prueba de concurrencia.** La fila se bloquea con
    `select_for_update()` y su estado se relee ya con el lock tomado: si otra ejecución
    se adelantó, esta se retira sin enviar. Sin ese bloqueo, dos entregas simultáneas
    del mismo mensaje —algo perfectamente posible en Celery— producirían dos correos.

    Consecuencia asumida: **el lock de la fila dura lo que tarde en responder Brevo**.
    Es aceptable en este MVP —el volumen es de unos pocos correos de verificación, y el
    cliente impone un timeout explícito de 15 s— y la alternativa sería añadir un estado
    intermedio `ENVIANDO` al enumerado, es decir, cambiar el esquema para optimizar algo
    que todavía no duele.

    **Reintentos acotados (Sprint 13, S13-D11).** Un fallo transitorio del proveedor
    —red, timeout, 5xx o 429— no marca FALLIDO mientras queden intentos: la fila sigue
    PENDIENTE con `next_attempt_at` y la retoma el barrido `dispatch_due`. Un fallo
    permanente, o el último intento, la deja FALLIDO, que sigue siendo terminal. Una
    PENDIENTE cuyo `next_attempt_at` todavía no llegó no se envía: así, dos tareas sobre
    la misma fila nunca duplican ni adelantan un reintento.

    `now` solo existe para las pruebas; la tarea de Celery llama sin él.
    """
    now = now or timezone.now()
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

        if notificacion.next_attempt_at is not None and notificacion.next_attempt_at > now:
            # Reintento programado que todavía no toca: lo retoma el barrido.
            return notificacion

        if not notificacion.user.is_active:
            # Destinatario dado de baja entre la creación y el envío: fallo permanente.
            # Se cuenta el intento para que el admin vea que se procesó (CT-51).
            notificacion.attempts += 1
            notificacion.status = NotificationStatus.FALLIDO
            notificacion.next_attempt_at = None
            notificacion.save(update_fields=["status", "attempts", "next_attempt_at", "updated_at"])
            logger.warning(
                "La notificación %s no se envió: destinatario inactivo.", notificacion.pk
            )
            return notificacion

        notificacion.attempts += 1
        try:
            _enviar_email(notificacion)
        except Exception as exc:
            # Se captura de forma amplia y a propósito: cualquier fallo del correo debe
            # terminar en un estado explícito. Dejar escapar la excepción dejaría la fila
            # PENDIENTE sin nadie que la reprograme.
            #
            # Del error se registra **solo el tipo**, nunca su mensaje: un rechazo del
            # proveedor suele incluir la dirección del destinatario. El id de la
            # notificación basta para investigar el caso en el admin.
            if _es_transitorio(exc) and notificacion.attempts < retry.MAX_ATTEMPTS:
                notificacion.next_attempt_at = now + retry.BACKOFF[notificacion.attempts - 1]
                notificacion.save(update_fields=["attempts", "next_attempt_at", "updated_at"])
                logger.warning(
                    "Falló el envío de la notificación %s (%s); se reintenta.",
                    notificacion.pk,
                    type(exc).__name__,
                )
            else:
                notificacion.status = NotificationStatus.FALLIDO
                notificacion.next_attempt_at = None
                notificacion.save(
                    update_fields=["status", "attempts", "next_attempt_at", "updated_at"]
                )
                logger.warning(
                    "Falló el envío de la notificación %s (%s); queda FALLIDO.",
                    notificacion.pk,
                    type(exc).__name__,
                )
        else:
            notificacion.status = NotificationStatus.ENVIADO
            notificacion.next_attempt_at = None
            notificacion.save(update_fields=["status", "attempts", "next_attempt_at", "updated_at"])

    return notificacion


def dispatch_due(*, now=None) -> int:
    """Barrido periódico de reintentos (Beat, cada 5 min). Devuelve cuántas encoló.

    Encola las PENDIENTE cuyo reintento ya venció y rescata las que nunca tuvieron un
    intento porque su encolado se perdió, si tienen entre 15 min y 24 h. Ninguna
    de las dos ramas toma notificaciones de más de 24 h: nunca salen solas. No cambia estados: el
    envío —y su lock— sigue siendo de `send()`. Un fallo al encolar solo se registra;
    la siguiente corrida lo vuelve a intentar.
    """
    from apps.notifications_app.tasks import send_notification

    now = now or timezone.now()
    vencidas = Q(
        attempts__gt=0,
        next_attempt_at__lte=now,
        created_at__gte=now - retry.RESCUE_WINDOW,
    )
    perdidas = Q(
        attempts=0,
        created_at__lte=now - retry.RESCUE_AFTER,
        created_at__gte=now - retry.RESCUE_WINDOW,
    )
    ids = list(
        Notification.objects.filter(status=NotificationStatus.PENDIENTE)
        .filter(vencidas | perdidas)
        .order_by("next_attempt_at", "created_at")
        .values_list("pk", flat=True)[: retry.SWEEP_LIMIT]
    )

    encoladas = 0
    for notification_id in ids:
        try:
            send_notification.delay(notification_id)
        except Exception as exc:
            logger.warning(
                "No se pudo encolar el reintento de la notificación %s (%s).",
                notification_id,
                type(exc).__name__,
            )
        else:
            encoladas += 1
    return encoladas


def _es_transitorio(exc: Exception) -> bool:
    """Red, timeout, 5xx o 429 del proveedor. Todo lo demás es permanente."""
    if not isinstance(exc, brevo.BrevoUnavailableError):
        return False
    estado = exc.status_code
    return estado is None or estado >= 500 or estado == 429


def resend(notification_id) -> Notification:
    """Devuelve una notificación FALLIDA a PENDIENTE y vuelve a encolar su envío.

    Es el **reintento manual** del §14 del plan: lo dispara una persona con
    `resend_notification`, caso por caso, y queda en `audit_logs`. No es —ni se
    convierte en— un reintento automático: `send()` sigue tratando FALLIDO como terminal
    y la tarea de Celery sigue sin `autoretry_for`. Los reintentos automáticos del
    Sprint 13 solo actúan sobre PENDIENTE; este reenvío reinicia su contador de intentos.

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
        # El reenvío manual arranca un ciclo nuevo de intentos (S13-D11).
        notificacion.attempts = 0
        notificacion.next_attempt_at = None
        notificacion.save(update_fields=["status", "attempts", "next_attempt_at", "updated_at"])

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
    """Contexto mínimo de cada plantilla. Nunca datos sensibles ni códigos.

    Los tipos de S4 reciben exactamente `{"nombre"}`. El canje suma institución y
    recompensa. Los eventos de reporte suman la fecha y, salvo el de creación, la
    categoría vigente y la institución que actuó: el de creación no nombra la categoría
    porque la IA puede reclasificar el reporte mientras el correo sale (S13-D09). Si falta
    el objeto que el tipo necesita, `ValueError`: un fallo permanente, mejor que un
    correo inventado.
    """
    contexto = {"nombre": notificacion.user.full_name or notificacion.user.email.split("@")[0]}
    tipo = notificacion.notification_type
    if tipo == NotificationType.CANJE_REALIZADO:
        if notificacion.redemption_id is None:
            raise ValueError("Un aviso de canje requiere su vale.")
        redemption = notificacion.redemption
        contexto.update(
            institucion=redemption.institution.name,
            recompensa=redemption.reward.name,
        )
    elif tipo in TIPOS_DE_REPORTE:
        if notificacion.report_id is None:
            raise ValueError("Un aviso de reporte requiere su reporte.")
        report = notificacion.report
        contexto["fecha"] = timezone.localtime(report.created_at).strftime("%d/%m/%Y")
        if tipo != NotificationType.REPORTE_CREADO:
            if notificacion.institution_id is None:
                raise ValueError("Un aviso de decisión institucional requiere la institución.")
            contexto.update(
                categoria=report.category.name,
                institucion=notificacion.institution.name,
            )
    elif tipo == NotificationType.RECOMPENSA_DISPONIBLE:
        if notificacion.institution_id is None:
            raise ValueError("Un aviso de recompensa disponible requiere la institución.")
        contexto["institucion"] = notificacion.institution.name
    return contexto
