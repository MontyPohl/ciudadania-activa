from django.conf import settings
from django.db import models

from apps.core.models import BaseModel
from apps.notifications_app.enums import (
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)
from apps.reports_app.models import Report


class Notification(BaseModel):
    """Correo enviado o pendiente de envío (Anexo A.1, RF-48).

    El servicio de envío mínimo llega en el Sprint 4 y se completa en S12-S13.
    """

    # CASCADE: un aviso personal no tiene valor de auditoría propio; la trazabilidad
    # de la acción que lo originó vive en audit_logs.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    # Nullable: los correos de verificación de identidad no se refieren a un reporte.
    report = models.ForeignKey(
        Report,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    redemption = models.ForeignKey(
        "rewards_app.RewardRedemption",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    notification_type = models.CharField(max_length=40, choices=NotificationType.choices)
    channel = models.CharField(
        max_length=10,
        choices=NotificationChannel.choices,
        default=NotificationChannel.EMAIL,
    )
    status = models.CharField(
        max_length=10,
        choices=NotificationStatus.choices,
        default=NotificationStatus.PENDIENTE,
    )

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.user_id}"
