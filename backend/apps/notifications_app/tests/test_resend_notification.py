"""Reintento manual de una notificación FALLIDA: servicio y comando de gestión.

**Ninguna prueba contacta con Brevo ni con un broker real.** El doble `brevo_api` es
`autouse`, y la suite corre con `CELERY_TASK_ALWAYS_EAGER`, de modo que `.delay()`
ejecuta en el acto salvo que la prueba lo sustituya para observar el encolado.

Lo que se protege aquí es que el reintento siga siendo **manual**: que FALLIDO no se
reactive solo, que solo un operador pueda hacerlo, que quede auditado sin datos
personales, y que dos ejecuciones simultáneas no produzcan dos correos.
"""

import logging
import threading
import uuid

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.admin_app.models import AuditLog
from apps.core.enums import ActorType
from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch

ESPERA_MAXIMA = 15


@pytest.fixture
def sin_encolar(monkeypatch):
    """Sustituye `send_notification.delay` y registra a qué se le llamó.

    Con `CELERY_TASK_ALWAYS_EAGER`, `.delay()` ejecutaría el envío completo en el acto.
    La mayoría de estas pruebas quiere observar **el encolado**, no el envío, así que se
    corta ahí. Las que sí ejercitan el camino completo no piden esta fixture.
    """
    from apps.notifications_app import tasks

    llamadas = []
    monkeypatch.setattr(
        tasks.send_notification, "delay", lambda notification_id: llamadas.append(notification_id)
    )
    return type("Encolador", (), {"llamadas": llamadas})()


@pytest.fixture
def fallida(citizen):
    """Notificación en el estado exacto en el que quedó la de producción."""
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)
    notificacion.status = NotificationStatus.FALLIDO
    notificacion.save(update_fields=["status"])
    return notificacion


def _reenviar(notificacion) -> None:
    """Ejecuta el comando. Los efectos de `on_commit` **no** ocurren aquí.

    En una prueba `django_db` normal, Django envuelve todo en una transacción que nunca
    se confirma, así que los callbacks quedan pendientes. Las pruebas que necesitan
    observar el encolado usan `_reenviar_confirmando`.
    """
    call_command("resend_notification", str(notificacion.pk))


def _reenviar_confirmando(notificacion, capturar) -> None:
    """Ejecuta el comando y dispara los callbacks de `on_commit`."""
    with capturar(execute=True):
        _reenviar(notificacion)


# --------------------------------------------------------------------------- #
# El camino feliz
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_una_notificacion_fallida_vuelve_a_pendiente(fallida, sin_encolar):
    _reenviar(fallida)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.PENDIENTE


@pytest.mark.django_db
def test_el_reenvio_completo_deja_la_notificacion_enviada(
    fallida, brevo_api, django_capture_on_commit_callbacks
):
    """De extremo a extremo con la tarea eager: PENDIENTE, envío y ENVIADO."""
    _reenviar_confirmando(fallida, django_capture_on_commit_callbacks)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.ENVIADO
    assert brevo_api.envios == 1


@pytest.mark.django_db
def test_el_comando_informa_del_resultado(fallida, sin_encolar, capsys):
    _reenviar(fallida)

    assert str(fallida.pk) in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Auditoría
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_se_registra_la_accion_de_reenvio(fallida, sin_encolar, citizen):
    _reenviar(fallida)

    entrada = AuditLog.objects.get()
    assert entrada.action == "notification.resent"
    assert entrada.action == dispatch.ACCION_REENVIO
    assert entrada.payload == {
        "notification_id": str(fallida.pk),
        "notification_type": NotificationType.VERIFICACION_APROBADA,
        "target_user_id": str(citizen.pk),
    }


@pytest.mark.django_db
def test_el_actor_es_el_sistema(fallida, sin_encolar):
    """Como `promote_user`: lo ejecuta la consola, no un usuario autenticado."""
    _reenviar(fallida)

    entrada = AuditLog.objects.get()
    assert entrada.actor_user is None
    assert entrada.actor_type == ActorType.SYSTEM


@pytest.mark.django_db
def test_la_auditoria_no_lleva_datos_sensibles(fallida, sin_encolar, citizen):
    """Ni el destinatario, ni el cuerpo, ni el fallo anterior, ni ninguna clave."""
    _reenviar(fallida)

    texto = str(AuditLog.objects.get().payload)
    assert citizen.email not in texto
    assert "@" not in texto
    for prohibido in ("Tu identidad", "api-key", "BREVO", "brevo", "TimeoutError", "smtp"):
        assert prohibido not in texto


@pytest.mark.django_db
def test_la_auditoria_se_escribe_en_la_misma_transaccion(fallida, monkeypatch):
    """Si el encolado no llega a ocurrir por un fallo previo, no queda rastro parcial."""
    monkeypatch.setattr(
        dispatch,
        "_payload_reenvio",
        lambda notificacion: 1 / 0,  # noqa: ARG005
    )

    with pytest.raises(ZeroDivisionError):
        dispatch.resend(fallida.pk)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.FALLIDO
    assert not AuditLog.objects.exists()


# --------------------------------------------------------------------------- #
# Encolado
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_la_tarea_se_encola_una_sola_vez(fallida, sin_encolar, django_capture_on_commit_callbacks):
    _reenviar_confirmando(fallida, django_capture_on_commit_callbacks)

    assert sin_encolar.llamadas == [fallida.pk]


@pytest.mark.django_db
def test_el_encolado_ocurre_despues_del_commit(
    fallida, sin_encolar, django_capture_on_commit_callbacks
):
    """Dentro de la transacción no se encola nada; al confirmar, exactamente una tarea."""
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        dispatch.resend(fallida.pk)
        assert sin_encolar.llamadas == []

    assert len(callbacks) == 1

    for callback in callbacks:
        callback()

    assert sin_encolar.llamadas == [fallida.pk]


@pytest.mark.django_db
def test_si_la_transaccion_se_deshace_no_se_encola_nada(fallida, sin_encolar, monkeypatch):
    """`on_commit` no dispara sobre una transacción que no llegó a confirmarse."""

    def explota(*args, **kwargs):
        raise RuntimeError("fallo simulado después de auditar")

    monkeypatch.setattr(dispatch.transaction, "on_commit", explota)

    with pytest.raises(RuntimeError):
        dispatch.resend(fallida.pk)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.FALLIDO
    assert sin_encolar.llamadas == []
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_si_el_broker_falla_la_notificacion_vuelve_a_fallido(
    fallida, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    """Sin esto quedaría en PENDIENTE sin nadie que la procese y sin poder reintentarla.

    La entrada de auditoría **se conserva**: alguien intentó el reenvío, y eso ocurrió.
    """
    from apps.notifications_app import tasks

    def broker_caido(*args, **kwargs):
        raise ConnectionError("broker inalcanzable")

    monkeypatch.setattr(tasks.send_notification, "delay", broker_caido)

    with caplog.at_level(logging.WARNING):
        _reenviar_confirmando(fallida, django_capture_on_commit_callbacks)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.FALLIDO
    assert AuditLog.objects.count() == 1
    assert "ConnectionError" in caplog.text


@pytest.mark.django_db
def test_tras_un_fallo_de_broker_el_comando_se_puede_repetir(
    fallida, monkeypatch, brevo_api, django_capture_on_commit_callbacks
):
    """La recuperación no deja al operador sin salida, y no necesita SQL directo.

    Primer intento con el broker caído: la notificación vuelve a FALLIDO. Segundo
    intento, ya con el broker en pie: el **mismo** comando la reenvía. Sin esto, la
    notificación quedaría en PENDIENTE y el comando la rechazaría para siempre, porque
    exige FALLIDO.
    """
    from apps.notifications_app import tasks

    delay_real = tasks.send_notification.delay

    def broker_caido(*args, **kwargs):
        raise ConnectionError("broker inalcanzable")

    monkeypatch.setattr(tasks.send_notification, "delay", broker_caido)
    _reenviar_confirmando(fallida, django_capture_on_commit_callbacks)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.FALLIDO

    # El operador arregla el broker y repite exactamente el mismo comando.
    monkeypatch.setattr(tasks.send_notification, "delay", delay_real)
    _reenviar_confirmando(fallida, django_capture_on_commit_callbacks)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.ENVIADO
    assert brevo_api.envios == 1
    # Los dos intentos quedan registrados: el fallido también ocurrió.
    assert AuditLog.objects.filter(action=dispatch.ACCION_REENVIO).count() == 2


# --------------------------------------------------------------------------- #
# Estados que no admiten reenvío
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_una_notificacion_inexistente_es_un_error(sin_encolar):
    with pytest.raises(CommandError, match="No existe"):
        call_command("resend_notification", str(uuid.uuid4()))

    assert sin_encolar.llamadas == []
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_un_identificador_mal_formado_es_un_error(sin_encolar):
    with pytest.raises(CommandError, match="no es un UUID válido"):
        call_command("resend_notification", "esto-no-es-un-uuid")

    assert sin_encolar.llamadas == []


@pytest.mark.django_db
@pytest.mark.parametrize("estado", [NotificationStatus.PENDIENTE, NotificationStatus.ENVIADO])
def test_solo_se_reenvia_lo_que_esta_fallido(citizen, sin_encolar, estado):
    """PENDIENTE ya está en camino y ENVIADO ya salió: reactivar duplicaría el correo."""
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)
    notificacion.status = estado
    notificacion.save(update_fields=["status"])

    with pytest.raises(CommandError, match="FALLIDO"):
        _reenviar(notificacion)

    notificacion.refresh_from_db()
    assert notificacion.status == estado
    assert sin_encolar.llamadas == []
    assert not AuditLog.objects.exists()


@pytest.mark.django_db
def test_el_comando_no_toca_ninguna_otra_notificacion(
    fallida, citizen, sin_encolar, django_capture_on_commit_callbacks
):
    otra = dispatch.create(citizen, NotificationType.VERIFICACION_RECHAZADA)
    otra.status = NotificationStatus.FALLIDO
    otra.save(update_fields=["status"])

    _reenviar_confirmando(fallida, django_capture_on_commit_callbacks)

    otra.refresh_from_db()
    assert otra.status == NotificationStatus.FALLIDO
    assert sin_encolar.llamadas == [fallida.pk]
    assert AuditLog.objects.count() == 1


# --------------------------------------------------------------------------- #
# El reenvío sigue siendo manual
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_el_comando_no_habla_con_brevo(fallida, brevo_api, sin_encolar):
    """El comando reencola; el envío lo hace el worker. Sin la tarea, no hay llamada."""
    _reenviar(fallida)

    assert brevo_api.envios == 0


@pytest.mark.django_db
def test_send_sigue_tratando_fallido_como_terminal(fallida, brevo_api):
    """El reintento manual no debilita la regla: reencolar la tarea no basta."""
    dispatch.send(fallida.pk)

    fallida.refresh_from_db()
    assert fallida.status == NotificationStatus.FALLIDO
    assert brevo_api.envios == 0


@pytest.mark.django_db
def test_la_tarea_sigue_sin_reintentos_automaticos():
    from apps.notifications_app.tasks import send_notification

    assert getattr(send_notification, "autoretry_for", ()) == ()
    assert getattr(send_notification, "retry_backoff", False) is False


@pytest.mark.django_db
def test_ninguna_clave_aparece_en_los_logs(fallida, settings, caplog):
    with caplog.at_level(logging.DEBUG):
        _reenviar(fallida)

    assert settings.BREVO_API_KEY not in caplog.text
    assert "api-key" not in caplog.text


# --------------------------------------------------------------------------- #
# Concurrencia
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_dos_operadores_simultaneos_reactivan_una_sola_vez(user_factory, brevo_api):
    """Realista: dos hilos compitiendo de verdad contra PostgreSQL.

    Uno pasa la notificación a PENDIENTE; el otro, al tomar el lock, ya la ve PENDIENTE
    y recibe `ResendError`. Nunca dos correos ni dos entradas de auditoría.
    """
    citizen = user_factory(email="reenvio-concurrente@example.com")
    notificacion = dispatch.create(citizen, NotificationType.VERIFICACION_APROBADA)
    notificacion.status = NotificationStatus.FALLIDO
    notificacion.save(update_fields=["status"])

    barrera = threading.Barrier(2, timeout=ESPERA_MAXIMA)
    resultados: dict[int, str] = {}

    def reactivar(indice: int) -> None:
        try:
            barrera.wait()
            dispatch.resend(notificacion.pk)
            resultados[indice] = "exito"
        except dispatch.ResendError:
            resultados[indice] = "rechazado"
        except Exception as exc:  # pragma: no cover - solo si algo se rompe de verdad
            resultados[indice] = f"inesperado: {exc!r}"
        finally:
            connection.close()

    hilos = [threading.Thread(target=reactivar, args=(i,)) for i in (0, 1)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=ESPERA_MAXIMA)
        assert not hilo.is_alive(), "Un hilo quedó bloqueado: posible interbloqueo."

    assert sorted(resultados.values()) == ["exito", "rechazado"], resultados
    assert AuditLog.objects.filter(action=dispatch.ACCION_REENVIO).count() == 1
    assert brevo_api.envios == 1
    assert Notification.objects.get(pk=notificacion.pk).status == NotificationStatus.ENVIADO
