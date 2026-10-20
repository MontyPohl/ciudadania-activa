"""Tarea de Celery de envío de notificaciones.

La suite corre con `CELERY_TASK_ALWAYS_EAGER = True`, así que `.delay()` ejecuta en el
acto y no hace falta broker. La entrega va contra el doble de `requests.post`: nada sale
a la red.

La prueba que justifica el bloqueo de fila está al final: dos ejecuciones **de verdad
concurrentes** de la misma tarea deben producir un solo correo.
"""

import threading

import pytest
import requests
from django.db import connection

from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch
from apps.notifications_app.tasks import send_notification

ESPERA_MAXIMA = 15


@pytest.mark.django_db
def test_la_tarea_entrega_la_notificacion(citizen, brevo_api):
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification(notificacion.pk)

    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.ENVIADO
    assert brevo_api.envios == 1


@pytest.mark.django_db
def test_la_tarea_usa_la_api_https_de_brevo(citizen, brevo_api, settings):
    """El contrato de la tarea no cambia; sí el transporte que hay debajo (ADR-0015)."""
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification(notificacion.pk)

    assert brevo_api.ultimo.url == settings.BREVO_API_URL
    assert brevo_api.ultimo.headers["api-key"] == settings.BREVO_API_KEY
    assert brevo_api.ultimo.headers["content-type"] == "application/json"
    assert brevo_api.destinatario == citizen.email


@pytest.mark.django_db
def test_la_tarea_es_idempotente(citizen, brevo_api):
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification(notificacion.pk)
    send_notification(notificacion.pk)

    assert brevo_api.envios == 1


@pytest.mark.django_db
def test_la_tarea_no_propaga_el_fallo_de_red(citizen, brevo_api):
    """Relanzar solo ensuciaría el broker: el estado es el registro.

    Desde S13 un fallo de red es transitorio: la fila queda PENDIENTE y reprogramada, y
    la retoma el barrido de Beat, no un reintento de Celery.
    """
    brevo_api.excepcion = requests.exceptions.ReadTimeout("timeout")
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)

    send_notification(notificacion.pk)

    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.PENDIENTE
    assert notificacion.attempts == 1
    assert notificacion.next_attempt_at is not None


@pytest.mark.django_db
def test_la_tarea_sobre_una_notificacion_fallida_no_hace_nada(citizen, brevo_api):
    """Comportamiento actual documentado: FALLIDO es terminal (ver `test_dispatch`).

    Reencolar la tarea sobre la notificación que falló en producción **no la recupera**.
    Recuperarla exige una acción explícita que la devuelva a PENDIENTE.
    """
    brevo_api.status_code = 400  # permanente: FALLIDO al primer intento (S13)
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)
    send_notification(notificacion.pk)
    envios = brevo_api.envios

    brevo_api.status_code = 201
    send_notification(notificacion.pk)

    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.FALLIDO
    assert brevo_api.envios == envios


@pytest.mark.django_db
def test_la_tarea_no_declara_reintentos_automaticos():
    """La tarea no reintenta: los reintentos de S13 viven en la base y en el barrido."""
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
def test_dos_ejecuciones_concurrentes_producen_un_solo_correo(user_factory, brevo_api):
    """El `select_for_update` de `dispatch.send` es lo único que impide el duplicado.

    Celery no garantiza entrega única: un reenvío del broker o un doble consumo pueden
    ejecutar la misma tarea dos veces a la vez. Sin el bloqueo, ambas leerían PENDIENTE
    y ambas enviarían. Que el transporte sea HTTPS en vez de SMTP no cambia nada aquí:
    el duplicado lo evita la base de datos, no el proveedor.
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
    assert brevo_api.envios == 1
    assert Notification.objects.get(pk=notificacion.pk).status == NotificationStatus.ENVIADO
