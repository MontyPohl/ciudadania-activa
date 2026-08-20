import pytest
from django.utils import timezone

from apps.social_ingestion_app.models import TelegramIngestedMessage, XMockMessage

pytestmark = pytest.mark.django_db


def test_mensaje_de_telegram_sin_reporte_asociado():
    mensaje = TelegramIngestedMessage.objects.create(
        telegram_chat_id="12345", sender_alias="vecino", raw_text="Hay un bache enorme."
    )

    assert mensaje.report is None
    assert str(mensaje) == "telegram:12345"


def test_mensaje_de_x_simulado_conserva_su_fecha_simulada():
    simulado = timezone.now()
    mensaje = XMockMessage.objects.create(
        source_handle="@vecino_asu", raw_text="Sin luz en el barrio.", simulated_at=simulado
    )

    assert mensaje.simulated_at == simulado
    assert mensaje.created_at != simulado or mensaje.report is None
    assert str(mensaje) == "x_mock:@vecino_asu"


def test_borrar_el_reporte_conserva_el_mensaje_ingestado(report):
    telegram = TelegramIngestedMessage.objects.create(
        telegram_chat_id="9", raw_text="t", report=report
    )
    x_mock = XMockMessage.objects.create(
        source_handle="@x", raw_text="t", simulated_at=timezone.now(), report=report
    )

    report.delete()
    telegram.refresh_from_db()
    x_mock.refresh_from_db()

    assert telegram.report is None
    assert x_mock.report is None
