"""Servicio de notificaciones: creación y envío por la API HTTPS de Brevo.

Ninguna prueba envía correo real: el doble `brevo_api` del `conftest` raíz sustituye
`requests.post` y registra lo que se le pide enviar.

Lo que más se comprueba aquí no es que el correo llegue, sino **qué no lleva**: ni la
cédula, ni el HMAC, ni la selfie, ni su clave de objeto, ni el motivo libre del rechazo.
"""

import logging

import pytest
import requests

from apps.notifications_app.enums import NotificationChannel, NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import brevo, dispatch

pytestmark = pytest.mark.django_db


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


def test_create_no_envia_nada(citizen, brevo_api):
    _crear(citizen)

    assert brevo_api.envios == 0


def test_create_admite_el_tipo_de_rechazo(citizen):
    notificacion = _crear(citizen, NotificationType.VERIFICACION_RECHAZADA)

    assert notificacion.notification_type == NotificationType.VERIFICACION_RECHAZADA


# --------------------------------------------------------------------------- #
# send — camino feliz
# --------------------------------------------------------------------------- #


def test_send_entrega_y_marca_enviado(citizen, brevo_api):
    notificacion = _crear(citizen)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.ENVIADO
    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.ENVIADO
    assert brevo_api.envios == 1
    assert brevo_api.destinatario == citizen.email


def test_el_201_de_brevo_es_el_camino_normal(citizen, brevo_api):
    """Brevo acepta el mensaje con `201`, no con `200`."""
    brevo_api.status_code = 201

    assert dispatch.send(_crear(citizen).pk).status == NotificationStatus.ENVIADO


def test_la_plantilla_de_aprobacion(citizen, brevo_api):
    citizen.full_name = "Ana Benítez"
    citizen.save(update_fields=["full_name"])
    dispatch.send(_crear(citizen).pk)

    correo = brevo_api.ultimo
    assert correo.asunto == "Tu identidad fue verificada"
    assert "Ana Benítez" in correo.cuerpo
    assert "aprobada" in correo.cuerpo.lower()


def test_la_plantilla_de_rechazo(citizen, brevo_api):
    citizen.full_name = "Ana Benítez"
    citizen.save(update_fields=["full_name"])
    dispatch.send(_crear(citizen, NotificationType.VERIFICACION_RECHAZADA).pk)

    correo = brevo_api.ultimo
    assert correo.asunto == "Sobre tu verificación de identidad"
    assert "no pudo ser aprobada" in correo.cuerpo
    # Remite a la plataforma en vez de mandar el detalle por correo.
    assert "plataforma" in correo.cuerpo.lower()


def test_el_correo_de_aprobacion_no_promete_funcionalidades_futuras(citizen, brevo_api):
    """Solo se comunica lo que ya es cierto: reportes, ranking y canjes no existen aún."""
    dispatch.send(_crear(citizen).pk)

    cuerpo = brevo_api.ultimo.cuerpo.lower()
    for pendiente in ("reporte", "ranking", "punto", "crédito", "recompensa", "canje", "confirmar"):
        assert pendiente not in cuerpo, pendiente


def test_el_rechazo_no_manda_el_motivo_por_correo(citizen, brevo_api):
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

    enviado = str(brevo_api.ultimo.payload)
    assert motivo not in enviado
    assert "pulgar" not in enviado


def test_sin_nombre_se_usa_la_parte_local_del_email(user_factory, brevo_api):
    user = user_factory(email="sin.nombre@example.com")
    dispatch.send(_crear(user).pk)

    assert "sin.nombre" in brevo_api.ultimo.cuerpo


def test_la_peticion_no_lleva_datos_sensibles(citizen, brevo_api):
    from apps.auth_app.models import CitizenProfile

    perfil = CitizenProfile.objects.create(
        user=citizen,
        document_hmac="b" * 64,
        verification_selfie_url="verifications/abc/def.jpg",
    )
    dispatch.send(_crear(citizen).pk)

    enviado = str(brevo_api.ultimo.payload)
    assert perfil.document_hmac not in enviado
    assert perfil.verification_selfie_url not in enviado
    assert "verifications/" not in enviado
    assert "supabase" not in enviado.lower()
    # El cuerpo tampoco lleva enlaces: la plataforma se alcanza sin que el correo guíe.
    assert "http" not in brevo_api.ultimo.cuerpo.lower()


def test_la_cedula_no_llega_a_la_peticion(citizen, brevo_api, document_hmac_secret):
    """El número no se persiste, así que `dispatch` no tiene de dónde sacarlo.

    La prueba lo fija igualmente: es la garantía que la enmienda de ADR-0006 sostiene, y
    el correo es el único punto del flujo que sale del sistema.
    """
    from apps.auth_app.models import CitizenProfile
    from apps.auth_app.services import identity

    cedula = "0123456"
    CitizenProfile.objects.create(
        user=citizen,
        document_hmac=identity.compute_document_hmac(cedula),
        verification_selfie_url="verifications/abc/def.jpg",
    )

    dispatch.send(_crear(citizen).pk)

    assert cedula not in str(brevo_api.ultimo.payload)


# --------------------------------------------------------------------------- #
# send — idempotencia
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("estado", [NotificationStatus.ENVIADO, NotificationStatus.FALLIDO])
def test_send_no_reenvia_lo_que_ya_no_esta_pendiente(citizen, brevo_api, estado):
    notificacion = _crear(citizen)
    Notification.objects.filter(pk=notificacion.pk).update(status=estado)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == estado
    assert brevo_api.envios == 0


def test_una_notificacion_fallida_no_se_reintenta_sola(citizen, brevo_api):
    """Comportamiento **actual y deliberado**: FALLIDO es terminal en el Sprint 4.

    Volver a invocar el envío sobre una notificación fallida no la reintenta y no la
    modifica: la devuelve tal cual. Los reintentos automáticos son alcance de los
    Sprints 12-13. Recuperar una notificación fallida exige una acción explícita que
    la devuelva a PENDIENTE; hoy no existe ninguna, y esta prueba lo deja escrito para
    que un cambio futuro sea deliberado y no accidental.
    """
    brevo_api.excepcion = requests.exceptions.ReadTimeout("timeout")
    notificacion = _crear(citizen)
    dispatch.send(notificacion.pk)
    envios_tras_el_fallo = brevo_api.envios
    notificacion.refresh_from_db()
    actualizada_en = notificacion.updated_at

    brevo_api.excepcion = None
    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.FALLIDO
    assert brevo_api.envios == envios_tras_el_fallo  # no hubo segundo intento
    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.FALLIDO
    assert notificacion.updated_at == actualizada_en  # ni siquiera se reescribió la fila


def test_dos_envios_secuenciales_producen_un_solo_correo(citizen, brevo_api):
    notificacion = _crear(citizen)

    dispatch.send(notificacion.pk)
    dispatch.send(notificacion.pk)

    assert brevo_api.envios == 1


def test_send_sobre_una_notificacion_inexistente_no_rompe(citizen, brevo_api, caplog):
    import uuid

    with caplog.at_level(logging.DEBUG):
        assert dispatch.send(uuid.uuid4()) is None

    assert brevo_api.envios == 0


# --------------------------------------------------------------------------- #
# send — fallos
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("estado", [400, 401, 402, 403, 429])
def test_un_rechazo_4xx_deja_la_notificacion_en_fallido(citizen, brevo_api, estado):
    brevo_api.status_code = estado
    notificacion = _crear(citizen)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.FALLIDO
    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.FALLIDO


@pytest.mark.parametrize("estado", [500, 502, 503])
def test_un_error_5xx_deja_la_notificacion_en_fallido(citizen, brevo_api, estado):
    brevo_api.status_code = estado
    notificacion = _crear(citizen)

    assert dispatch.send(notificacion.pk).status == NotificationStatus.FALLIDO


@pytest.mark.parametrize(
    "fallo",
    [
        requests.exceptions.ConnectTimeout("timeout"),
        requests.exceptions.ReadTimeout("timeout"),
    ],
)
def test_un_timeout_deja_la_notificacion_en_fallido(citizen, brevo_api, fallo):
    """El fallo exacto del E2E productivo del 23/08/2026, ahora sobre HTTPS."""
    brevo_api.excepcion = fallo
    notificacion = _crear(citizen)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.FALLIDO
    notificacion.refresh_from_db()
    assert notificacion.status == NotificationStatus.FALLIDO


def test_un_fallo_de_conexion_deja_la_notificacion_en_fallido(citizen, brevo_api):
    brevo_api.excepcion = requests.exceptions.ConnectionError("connection refused")

    assert dispatch.send(_crear(citizen).pk).status == NotificationStatus.FALLIDO


def test_sin_clave_de_api_la_notificacion_queda_fallida(citizen, brevo_api, settings):
    """Fallo cerrado: sin configuración no se intenta la llamada y queda constancia."""
    settings.BREVO_API_KEY = ""
    notificacion = _crear(citizen)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.FALLIDO
    assert brevo_api.envios == 0


def test_un_fallo_de_envio_no_propaga_la_excepcion(citizen, brevo_api):
    """Dejar escapar el error dejaría la fila PENDIENTE para siempre."""
    brevo_api.excepcion = OSError("network unreachable")

    dispatch.send(_crear(citizen).pk)  # no debe levantar


def test_el_log_del_fallo_no_lleva_el_email_ni_el_detalle(citizen, brevo_api, caplog):
    """Un rechazo del proveedor suele incluir la dirección del destinatario."""
    brevo_api.excepcion = brevo.BrevoUnavailableError(f"rechazado: {citizen.email} desconocido")
    notificacion = _crear(citizen)

    with caplog.at_level(logging.DEBUG):
        dispatch.send(notificacion.pk)

    assert citizen.email not in caplog.text
    assert "desconocido" not in caplog.text
    # Queda lo justo para investigar: el id de la fila y el tipo de error.
    assert str(notificacion.pk) in caplog.text
    assert "BrevoUnavailableError" in caplog.text


def test_la_clave_de_api_no_aparece_en_los_logs_del_servicio(citizen, brevo_api, caplog):
    brevo_api.status_code = 401
    notificacion = _crear(citizen)

    with caplog.at_level(logging.DEBUG):
        dispatch.send(notificacion.pk)

    assert "clave-brevo-de-prueba-no-real" not in caplog.text


def test_un_tipo_sin_plantilla_queda_fallido_y_no_envia(citizen, brevo_api):
    """Mejor una fila FALLIDO visible que un correo inventado."""
    notificacion = _crear(citizen, NotificationType.RECOMPENSA_DISPONIBLE)

    resultado = dispatch.send(notificacion.pk)

    assert resultado.status == NotificationStatus.FALLIDO
    assert brevo_api.envios == 0


def test_el_servicio_ya_no_usa_smtp():
    """Railway Free no ofrece SMTP saliente: la regresión debe notarse (ADR-0015)."""
    import inspect

    fuente = inspect.getsource(dispatch)
    assert "send_mail" not in fuente
    assert "django.core.mail" not in fuente
