from django.db import models

from apps.core.models import BaseModel
from apps.reports_app.models import Report


class XMockMessage(BaseModel):
    """Mensaje del dataset simulado de X/Twitter (Anexo A.1, CU-13).

    Es el único origen social del MVP: Telegram quedó fuera del alcance y su modelo se
    eliminó en el Sprint 13 (D-04, CT-43). `simulated_at` es la fecha simulada del
    dataset y no coincide con `created_at`, que registra cuándo se cargó la fila.
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
