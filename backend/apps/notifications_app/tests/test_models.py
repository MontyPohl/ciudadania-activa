import pytest

from apps.notifications_app.enums import (
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)
from apps.notifications_app.models import Notification

pytestmark = pytest.mark.django_db


def test_notificacion_nace_pendiente_por_email(citizen):
    notificacion = Notification.objects.create(
        user=citizen, notification_type=NotificationType.VERIFICACION_APROBADA
    )

    assert notificacion.channel == NotificationChannel.EMAIL
    assert notificacion.status == NotificationStatus.PENDIENTE
    assert notificacion.report is None
    assert str(notificacion) == f"VERIFICACION_APROBADA -> {citizen.pk}"


def test_el_mvp_solo_entrega_email():
    assert NotificationChannel.values == ["EMAIL"]


def test_estados_de_envio():
    assert NotificationStatus.values == ["PENDIENTE", "ENVIADO", "FALLIDO"]


def test_tipos_de_notificacion_previstos():
    assert NotificationType.values == [
        "VERIFICACION_APROBADA",
        "VERIFICACION_RECHAZADA",
        "REPORTE_CREADO",
        "VALIDACION_INSTITUCIONAL",
        "REPORTE_RESUELTO",
        "REPORTE_RECHAZADO",
        "CANJE_REALIZADO",
        "RECOMPENSA_DISPONIBLE",
    ]


def test_la_validacion_es_un_evento_y_no_un_estado_del_reporte():
    assert "REPORTE_VALIDADO" not in NotificationType.values
    assert NotificationType.VALIDACION_INSTITUCIONAL.label == "Reporte validado"


def test_borrar_el_reporte_conserva_la_notificacion(citizen, report):
    notificacion = Notification.objects.create(
        user=citizen, report=report, notification_type=NotificationType.REPORTE_CREADO
    )
    report.delete()
    notificacion.refresh_from_db()

    assert notificacion.report is None


def test_borrar_al_destinatario_arrastra_sus_notificaciones(citizen):
    Notification.objects.create(user=citizen, notification_type=NotificationType.CANJE_REALIZADO)
    citizen.delete()

    assert Notification.objects.count() == 0
