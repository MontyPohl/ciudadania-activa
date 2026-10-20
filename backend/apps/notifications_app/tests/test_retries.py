"""Reintentos automáticos acotados (Sprint 13, S13-D11).

Solo ante fallos transitorios y solo mientras la notificación sigue PENDIENTE. FALLIDO
sigue siendo terminal; `resend_notification` es su única recuperación (CT-51).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.notifications_app import retry
from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch

pytestmark = pytest.mark.django_db


def _crear(user):
    return dispatch.create(user, NotificationType.VERIFICACION_APROBADA)


def _estado(notificacion):
    notificacion.refresh_from_db()
    return notificacion.status, notificacion.attempts, notificacion.next_attempt_at


def test_fallo_transitorio_reprograma_y_agota(citizen, brevo_api):
    brevo_api.status_code = 503
    notificacion = _crear(citizen)
    t0 = timezone.now()

    dispatch.send(notificacion.pk, now=t0)
    assert _estado(notificacion) == (NotificationStatus.PENDIENTE, 1, t0 + timedelta(minutes=5))

    # Antes de tiempo no se envía ni se escribe nada.
    envios = brevo_api.envios
    dispatch.send(notificacion.pk, now=t0 + timedelta(minutes=4))
    assert brevo_api.envios == envios
    assert _estado(notificacion)[1] == 1

    t1 = t0 + timedelta(minutes=5)
    dispatch.send(notificacion.pk, now=t1)
    assert _estado(notificacion) == (NotificationStatus.PENDIENTE, 2, t1 + timedelta(minutes=30))

    dispatch.send(notificacion.pk, now=t1 + timedelta(minutes=30))
    assert _estado(notificacion) == (NotificationStatus.FALLIDO, retry.MAX_ATTEMPTS, None)

    # Un 429 es transitorio y el siguiente intento puede salir bien.
    brevo_api.status_code = 429
    otra = _crear(citizen)
    dispatch.send(otra.pk, now=t0)
    brevo_api.status_code = 201
    dispatch.send(otra.pk, now=t0 + timedelta(minutes=5))
    assert _estado(otra) == (NotificationStatus.ENVIADO, 2, None)


def test_barrido_reencola_lo_vencido_y_rescata_solo_lo_reciente(citizen, monkeypatch):
    ahora = timezone.now()
    encoladas = []
    monkeypatch.setattr("apps.notifications_app.tasks.send_notification.delay", encoladas.append)

    def fila(*, status=NotificationStatus.PENDIENTE, attempts=0, next_at=None, edad):
        notificacion = _crear(citizen)
        Notification.objects.filter(pk=notificacion.pk).update(
            status=status,
            attempts=attempts,
            next_attempt_at=next_at,
            created_at=ahora - edad,
        )
        return notificacion.pk

    vencida = fila(attempts=1, next_at=ahora - timedelta(minutes=1), edad=timedelta(hours=1))
    fila(attempts=1, next_at=ahora + timedelta(minutes=10), edad=timedelta(hours=1))
    perdida = fila(edad=timedelta(minutes=20))
    fila(edad=timedelta(minutes=2))
    fila(edad=timedelta(hours=30))
    # Un reintento vencido de más de 24 h tampoco sale solo.
    fila(attempts=1, next_at=ahora - timedelta(minutes=1), edad=timedelta(hours=25))
    fila(status=NotificationStatus.FALLIDO, attempts=3, edad=timedelta(hours=1))

    assert dispatch.dispatch_due(now=ahora) == 2
    assert set(encoladas) == {vencida, perdida}


def test_resend_reinicia_los_intentos(citizen, monkeypatch):
    monkeypatch.setattr("apps.notifications_app.tasks.send_notification.delay", lambda pk: None)
    notificacion = _crear(citizen)
    Notification.objects.filter(pk=notificacion.pk).update(
        status=NotificationStatus.FALLIDO, attempts=retry.MAX_ATTEMPTS
    )

    dispatch.resend(notificacion.pk)

    assert _estado(notificacion) == (NotificationStatus.PENDIENTE, 0, None)
