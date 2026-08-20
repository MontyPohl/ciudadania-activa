from django.db import models


class RewardTxStatus(models.TextChoices):
    """Estados de una transacción del ledger (Anexo A.2 + corrección C-01).

    `ANULADO` y `REVERTIDO` no son sinónimos: el primero cierra una expectativa
    que nunca se acreditó porque el reporte fue rechazado; el segundo deshace un
    derecho que sí llegó a existir. Solo `ACREDITADO` participa de los saldos.
    """

    PENDIENTE = "PENDIENTE", "Pendiente"
    ACREDITADO = "ACREDITADO", "Acreditado"
    ANULADO = "ANULADO", "Anulado"
    REVERTIDO = "REVERTIDO", "Revertido"


class RewardEventType(models.TextChoices):
    """Catálogo cerrado de eventos económicos (§6.5 del plan, CT-09)."""

    REPORT_CREATED = "REPORT_CREATED", "Reporte creado"
    CONFIRMATION_CREATED = "CONFIRMATION_CREATED", "Confirmación creada"
    VALIDATION_BONUS = "VALIDATION_BONUS", "Bonus por validación"
    RESOLUTION_BONUS = "RESOLUTION_BONUS", "Bonus por resolución"
    REDEMPTION = "REDEMPTION", "Canje"
    REVERSAL = "REVERSAL", "Reversión"
