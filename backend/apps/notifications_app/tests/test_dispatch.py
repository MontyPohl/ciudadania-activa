"""Servicio de notificaciones: creación y envío por email.

Ninguna prueba envía correo real: `config/settings/test.py` fija el backend `locmem`,
así que `django.core.mail.outbox` es todo lo que sale.

Lo que más se comprueba aquí no es que el correo llegue, sino **qué no lleva**: ni la
cédula, ni el HMAC, ni la selfie, ni su clave de objeto, ni el motivo libre del rechazo.
"""

import logging

import pytest
from django.core import mail

from apps.notifications_app.enums import NotificationChannel, NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def bandeja_limpia():
    mail.outbox.clear()
    yield
    mail.outbox.clear()


def _crear(user, tipo=NotificationType.VERIFICACION_APROBADA):
    return dispatch.create(user, tipo)


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


def test_create_deja_la_fila_pendiente_por_email(citizen):
    notificacion = _crear(citizen)

    assert notificacion.user == citizen
    assert notificacion.notification_type == NotificationType.VERIFICACION_APROBADA
    assert notificacion.channel == NotificationChannel.EMAIL
    assert notificacion.status == NotificationStatus.PENDIENTE
    assert notificacion.report is None


def test_create_no_envia_nada(citizen):
    _crear(citizen)

    assert mail.outbox == []


def test_create_admite_el_tipo_de_rechazo(citizen):
    notificacion = _crear(citizen, NotificationType.VERIFICACION_RECHAZADA)

    assert notificacion.notification_type == NotificationType.VERIFICACION_RECHAZADA


# --------------------------------------------------------------------------- #
# send — camino feliz
# --------------------------------------------------------------------------- #


def test_send_entrega_y_marca_enviado(citizen):
    notificacion = _crear(citizen)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.ENVIADO
    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.ENVIADO
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [citizen.email]


def test_la_plantilla_de_aprobacion(citizen):
    citizen.full_name = "Ana Benítez"
    citizen.save(update_fields=["full_name"])
    dispatch.send(_crear(citizen).pk)

    correo = mail.outbox[0]
    assert correo.subject == "Tu identidad fue verificada"
    assert "Ana Benítez" in correo.body
    assert "aprobada" in correo.body.lower()


def test_la_plantilla_de_rechazo(citizen):
    citizen.full_name = "Ana Benítez"
    citizen.save(update_fields=["full_name"])
    dispatch.send(_crear(citizen, NotificationType.VERIFICACION_RECHAZADA).pk)

    correo = mail.outbox[0]
    assert correo.subject == "Sobre tu verificación de identidad"
    assert "no pudo ser aprobada" in correo.body
    # Remite a la plataforma en vez de mandar el detalle por correo.
    assert "plataforma" in correo.body.lower()


def test_el_correo_de_aprobacion_no_promete_funcionalidades_futuras(citizen):
    """Solo se comunica lo que ya es cierto: reportes, ranking y canjes no existen aún."""
    dispatch.send(_crear(citizen).pk)

    cuerpo = mail.outbox[0].body.lower()
    for pendiente in ("reporte", "ranking", "punto", "crédito", "recompensa", "canje", "confirmar"):
        assert pendiente not in cuerpo, pendiente


def test_el_rechazo_no_manda_el_motivo_por_correo(citizen):
    """El motivo libre se consulta autenticado, no viaja por servidores de terceros."""
    from apps.auth_app.models import CitizenProfile
    from apps.auth_app.services import identity

    motivo = "La cédula está tapada por el pulgar en la foto."
    CitizenProfile.objects.create(
        user=citizen,
        document_hmac="a" * 64,
        verification_selfie_url="verifications/x/y.jpg",
        rejection_reason=motivo,
    )
    assert identity  # el módulo existe; aquí solo interesa el contenido del correo

    dispatch.send(_crear(citizen, NotificationType.VERIFICACION_RECHAZADA).pk)

    assert motivo not in mail.outbox[0].body
    assert "pulgar" not in mail.outbox[0].body


def test_sin_nombre_se_usa_la_parte_local_del_email(user_factory):
    user = user_factory(email="sin.nombre@example.com")
    dispatch.send(_crear(user).pk)

    assert "sin.nombre" in mail.outbox[0].body


def test_el_correo_no_lleva_datos_sensibles(citizen):
    from apps.auth_app.models import CitizenProfile

    perfil = CitizenProfile.objects.create(
        user=citizen,
        document_hmac="b" * 64,
        verification_selfie_url="verifications/abc/def.jpg",
    )
    dispatch.send(_crear(citizen).pk)

    cuerpo = mail.outbox[0].body
    assert perfil.document_hmac not in cuerpo
    assert perfil.verification_selfie_url not in cuerpo
    assert "verifications/" not in cuerpo
    assert "supabase" not in cuerpo.lower()
    assert "http" not in cuerpo.lower()


# --------------------------------------------------------------------------- #
# send — idempotencia
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("estado", [NotificationStatus.ENVIADO, NotificationStatus.FALLIDO])
def test_send_no_reenvia_lo_que_ya_no_esta_pendiente(citizen, estado):
    notificacion = _crear(citizen)
    Notification.objects.filter(pk=notificacion.pk).update(status=estado)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == estado
    assert mail.outbox == []


def test_dos_envios_secuenciales_producen_un_solo_correo(citizen):
    notificacion = _crear(citizen)

    dispatch.send(notificacion.pk)
    dispatch.send(notificacion.pk)

    assert len(mail.outbox) == 1


def test_send_sobre_una_notificacion_inexistente_no_rompe(citizen, caplog):
    import uuid

    with caplog.at_level(logging.DEBUG):
        assert dispatch.send(uuid.uuid4()) is None

    assert mail.outbox == []


# --------------------------------------------------------------------------- #
# send — fallos
# --------------------------------------------------------------------------- #


def test_un_fallo_de_smtp_deja_la_notificacion_en_fallido(citizen, monkeypatch):
    import smtplib

    def falla(*args, **kwargs):
        raise smtplib.SMTPException("Connection refused for ana@example.com")

    monkeypatch.setattr("apps.notifications_app.services.dispatch.send_mail", falla)
    notificacion = _crear(citizen)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.FALLIDO
    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.FALLIDO


def test_un_fallo_de_smtp_no_propaga_la_excepcion(citizen, monkeypatch):
    """Dejar escapar el error dejaría la fila PENDIENTE para siempre."""

    def falla(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr("apps.notifications_app.services.dispatch.send_mail", falla)

    dispatch.send(_crear(citizen).pk)  # no debe levantar


def test_el_log_del_fallo_no_lleva_el_mensaje_de_smtp_ni_el_email(citizen, monkeypatch, caplog):
    """Un rechazo de SMTP suele incluir la dirección del destinatario."""
    import smtplib

    def falla(*args, **kwargs):
        raise smtplib.SMTPRecipientsRefused({citizen.email: (550, b"User unknown")})

    monkeypatch.setattr("apps.notifications_app.services.dispatch.send_mail", falla)
    notificacion = _crear(citizen)

    with caplog.at_level(logging.DEBUG):
        dispatch.send(notificacion.pk)

    assert citizen.email not in caplog.text
    assert "User unknown" not in caplog.text
    # Queda lo justo para investigar: el id de la fila y el tipo de error.
    assert str(notificacion.pk) in caplog.text
    assert "SMTPRecipientsRefused" in caplog.text


def test_un_tipo_sin_plantilla_queda_fallido_y_no_envia(citizen):
    """Mejor una fila FALLIDO visible que un correo inventado."""
    notificacion = _crear(citizen, NotificationType.CANJE_REALIZADO)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.FALLIDO
    assert mail.outbox == []
