"""Revisión administrativa de la verificación de identidad (RF-03, RF-30, §30 del plan).

La aprobación es una **decisión humana**: no hay OCR, ni comparación biométrica, ni
ninguna heurística que decida por el administrador (ADR-0006). Este módulo solo se
ocupa de que esa decisión se aplique una sola vez, de forma atómica y auditada.

Tres invariantes que el código sostiene de forma explícita:

1. **Una identidad se revisa una sola vez.** El estado se relee con las filas ya
   bloqueadas; el segundo administrador recibe un conflicto, no sobrescribe al primero.
2. **La decisión y su aviso son atómicos.** La fila de `audit_logs` y la de
   `notifications` nacen en la misma transacción que la decisión: o están las tres o no
   está ninguna.
3. **El correo no puede tumbar la revisión.** El envío se encola *después* del commit y
   cualquier fallo al encolarlo se absorbe: la decisión ya está tomada y registrada.
"""

from __future__ import annotations

import logging
from functools import partial

from django.db import transaction
from django.utils import timezone

from apps.auth_app.enums import VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.core.services import audit

logger = logging.getLogger(__name__)

#: Acciones de auditoría de la revisión (§30 del plan).
ACCION_APROBACION = "identity.approved"
ACCION_RECHAZO = "identity.rejected"

#: Las dos únicas decisiones posibles, expresadas con la enumeración ya existente.
DECISIONES = (VerificationStatus.APROBADO, VerificationStatus.RECHAZADO)


class AlreadyReviewedError(Exception):
    """La identidad ya fue revisada, o otra petición se adelantó. Se traduce a 409."""


class InvalidReviewError(Exception):
    """La decisión recibida no es aplicable. Se traduce a 400."""


def review(profile_id, *, decision: str, reason: str = "", actor) -> CitizenProfile:
    """Aprueba o rechaza una verificación pendiente y devuelve el perfil actualizado.

    Todo ocurre en una sola transacción con las dos filas que se van a escribir
    —`citizen_profiles` y `users`— bloqueadas de forma **explícita**. El estado se
    relee ya con el lock tomado: comprobarlo antes no serviría de nada, porque entre la
    lectura y la escritura cabe otra transacción entera.

    El encolado del correo se registra con `transaction.on_commit`, de modo que si la
    transacción se deshace no se envía ningún aviso de una decisión que no existe.
    """
    if decision not in DECISIONES:
        raise InvalidReviewError("La decisión debe ser APROBADO o RECHAZADO.")

    motivo = (reason or "").strip()
    if decision == VerificationStatus.RECHAZADO and not motivo:
        raise InvalidReviewError("El rechazo requiere un motivo.")

    with transaction.atomic():
        perfil = _bloquear(profile_id)

        if perfil.user.verification_status != VerificationStatus.PENDIENTE:
            # Ya la revisó otro administrador, o el ciudadano no tiene nada pendiente.
            raise AlreadyReviewedError("Esta verificación ya fue revisada.")

        aprobado = decision == VerificationStatus.APROBADO

        perfil.verified_at = timezone.now()
        perfil.verified_by = actor
        perfil.rejection_reason = "" if aprobado else motivo
        perfil.save(update_fields=["verified_at", "verified_by", "rejection_reason", "updated_at"])

        perfil.user.verification_status = decision
        perfil.user.save(update_fields=["verification_status"])

        audit.log(
            actor,
            ACCION_APROBACION if aprobado else ACCION_RECHAZO,
            _payload_auditoria(perfil, motivo if not aprobado else None),
        )

        # Importación diferida: el §9 del plan no coloca `notifications_app` aguas
        # abajo de `admin_app`, y un importe en tiempo de módulo invertiría el grafo.
        from apps.notifications_app.enums import NotificationType
        from apps.notifications_app.services import dispatch

        notificacion = dispatch.create(
            perfil.user,
            NotificationType.VERIFICACION_APROBADA
            if aprobado
            else NotificationType.VERIFICACION_RECHAZADA,
        )

        transaction.on_commit(partial(_encolar_envio, notificacion.pk))

    return perfil


def _bloquear(profile_id) -> CitizenProfile:
    """Carga el perfil con **las dos filas** bloqueadas: `citizen_profiles` y `users`.

    El `of=("self", "user")` es explícito a propósito. Confiar en que `select_related`
    arrastre la tabla del usuario a la cláusula `FOR UPDATE` sería depender de un efecto
    lateral: lo que se bloquea debe leerse en el código, no deducirse del SQL. Hay una
    prueba que inspecciona el SQL emitido y comprueba que las dos tablas aparecen.
    """
    perfil = (
        CitizenProfile.objects.select_for_update(of=("self", "user"))
        .select_related("user")
        .filter(pk=profile_id)
        .first()
    )

    if perfil is None:
        raise CitizenProfile.DoesNotExist("No existe esa verificación.")

    return perfil


def _payload_auditoria(perfil, motivo: str | None) -> dict[str, str]:
    """Identificadores, y el motivo solo cuando se rechaza.

    El motivo **sí** forma parte de la auditoría: el §30 exige registrar la revisión
    «con actor y motivo». Todo lo demás queda fuera: ni la cédula, ni el HMAC, ni la
    clave del objeto, ni ninguna URL firmada, ni el email del ciudadano.
    """
    payload = {"profile_id": str(perfil.pk), "target_user_id": str(perfil.user_id)}
    if motivo is not None:
        payload["reason"] = motivo
    return payload


def _encolar_envio(notification_id) -> None:
    """Encola el correo. **Nunca deja escapar una excepción.**

    Corre en `on_commit`, es decir cuando la revisión ya está confirmada en base de
    datos. Django ejecuta estos callbacks al salir del bloque atómico y propaga lo que
    lancen, así que una excepción aquí llegaría hasta la respuesta HTTP y devolvería un
    500 por una decisión que **sí** se guardó. Eso sería mentirle al administrador.

    Si el broker no responde, la notificación pasa a FALLIDO: queda constancia de que el
    aviso no salió, sin tocar la revisión, que ya no se puede deshacer.
    """
    from apps.notifications_app.tasks import send_notification

    try:
        send_notification.delay(notification_id)
    except Exception as exc:
        logger.warning(
            "No se pudo encolar el envío de la notificación %s (%s).",
            notification_id,
            type(exc).__name__,
        )
        _marcar_fallida(notification_id)


def _marcar_fallida(notification_id) -> None:
    """Deja la notificación en FALLIDO. Tampoco propaga: es el último recurso.

    Si ni siquiera esta escritura prospera —la base acaba de caerse, por ejemplo— se
    registra el error y se termina. La alternativa sería propagar y convertir una
    revisión correcta en un 500.
    """
    from apps.notifications_app.enums import NotificationStatus
    from apps.notifications_app.models import Notification

    try:
        Notification.objects.filter(pk=notification_id).update(
            status=NotificationStatus.FALLIDO, updated_at=timezone.now()
        )
    except Exception as exc:
        logger.error(
            "No se pudo marcar como fallida la notificación %s (%s).",
            notification_id,
            type(exc).__name__,
        )
