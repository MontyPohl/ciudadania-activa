from django.db import models

from apps.core.models import BaseModel
from apps.reports_app.models import Report


class TelegramIngestedMessage(BaseModel):
    """Mensaje recibido por el bot de Telegram (Anexo A.1).

    La ingesta es condicional y pertenece al Sprint 13. El mensaje se conserva
    aunque su pre-reporte no llegue a materializarse, de ahí que `report` sea
    nullable y `SET_NULL`.
    """

    telegram_chat_id = models.CharField(max_length=64)
    sender_alias = models.CharField(max_length=120, blank=True)
    raw_text = models.TextField()
    report = models.ForeignKey(
        Report,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telegram_messages",
    )

    class Meta:
        db_table = "telegram_ingested_messages"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"telegram:{self.telegram_chat_id}"


class XMockMessage(BaseModel):
    """Mensaje del dataset simulado de X/Twitter (Anexo A.1).

    `simulated_at` es la fecha simulada del dataset y no coincide con `created_at`,
    que registra cuándo se cargó la fila.
    """

    source_handle = models.CharField(max_length=120)
    raw_text = models.TextField()
    simulated_at = models.DateTimeField()
    report = models.ForeignKey(
        Report,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="x_mock_messages",
    )

    class Meta:
        db_table = "x_mock_messages"
        ordering = ["-simulated_at"]

    def __str__(self) -> str:
        return f"x_mock:{self.source_handle}"
