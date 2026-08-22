"""Tarea de Celery de envío de notificaciones.

La suite corre con `CELERY_TASK_ALWAYS_EAGER = True`, así que `.delay()` ejecuta en el
acto y no hace falta broker. El envío usa el backend `locmem`: nada sale a la red.

La prueba que justifica el bloqueo de fila está al final: dos ejecuciones **de verdad
concurrentes** de la misma tarea deben producir un solo correo.
"""

import threading

import pytest
from django.core import mail
from django.db import connection

from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch
from apps.notifications_app.tasks import send_notification

ESPERA_MAXIMA = 15


@pytest.fixture(autouse=True)
def bandeja_limpia():
    mail.outbox.clear()
    yield
    mail.outbox.clear()


@pytest.mark.django_db
def test_la_tarea_entrega_la_notificacion(citizen):
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification(notificacion.pk)

    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.ENVIADO
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_la_tarea_es_idempotente(citizen):
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification(notificacion.pk)
    send_notification(notificacion.pk)

    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_la_tarea_no_propaga_el_fallo_de_smtp(citizen, monkeypatch):
    """Sin reintentos, relanzar solo ensuciaría el broker: el estado es el registro."""

    def falla(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr("apps.notifications_app.services.dispatch.send_mail", falla)
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification(notificacion.pk)

    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.FALLIDO


@pytest.mark.django_db
def test_la_tarea_no_declara_reintentos_automaticos():
    """Los reintentos son alcance de los Sprints 12-13 (§14 del plan)."""
    assert send_notification.max_retries in (None, 3)  # 3 es el default inerte de Celery
    assert getattr(send_notification, "autoretry_for", ()) == ()
    assert send_notification.name == "notifications.send_notification"


@pytest.mark.django_db
def test_delay_ejecuta_en_el_acto_en_la_suite(citizen, settings):
    assert settings.CELERY_TASK_ALWAYS_EAGER is True
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification.delay(notificacion.pk)

    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.ENVIADO


# --------------------------------------------------------------------------- #
# Concurrencia real
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_dos_ejecuciones_concurrentes_producen_un_solo_correo(user_factory):
    """El `select_for_update` de `dispatch.send` es lo único que impide el duplicado.

    Celery no garantiza entrega única: un reenvío del broker o un doble consumo pueden
    ejecutar la misma tarea dos veces a la vez. Sin el bloqueo, ambas leerían PENDIENTE
    y ambas enviarían.
    """
    citizen = user_factory(email="doble-envio@example.com")
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    barrera = threading.Barrier(2, timeout=ESPERA_MAXIMA)
    errores: list[str] = []

    def ejecutar() -> None:
        try:
            barrera.wait()
            send_notification(notificacion.pk)
        except Exception as exc:  # pragma: no cover - solo si algo se rompe de verdad
            errores.append(repr(exc))
        finally:
            connection.close()

    hilos = [threading.Thread(target=ejecutar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=ESPERA_MAXIMA)
        assert not hilo.is_alive(), "Un hilo quedó bloqueado: posible interbloqueo."

    assert errores == []
    assert len(mail.outbox) == 1
    assert Notification.objects.get(pk=notificacion.pk).status == NotificationStatus.ENVIADO
