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

    El servicio de envío mínimo llega en el Sprint 4; S12 suma el canje y S13 los eventos
    de reporte, la recompensa disponible y los reintentos automáticos acotados.
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
    # Institución que nombra el correo: la que validó, rechazó o resolvió el reporte, o la
    # de la recompensa disponible (Sprint 13). Nullable: identidad y canje no la usan.
    institution = models.ForeignKey(
        "admin_app.Institution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    # Clave de idempotencia de los avisos por evento (Sprint 13): un mismo evento nunca
    # produce dos correos. Nula en los tipos de S4 y S12, que no la necesitan; PostgreSQL
    # no considera iguales dos NULL, así que no chocan entre sí.
    dedup_key = models.CharField(max_length=160, null=True, blank=True)
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
    # Reintentos automáticos (Sprint 13, S13-D11): solo ante fallos transitorios y solo
    # mientras la fila sigue PENDIENTE. FALLIDO sigue siendo terminal (CT-51).
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="notification_due_idx"),
        ]
        constraints = [
            # Con nombre fijo: `dispatch.create` reconoce el choque por este nombre.
            models.UniqueConstraint(fields=["dedup_key"], name="notification_dedup_key_unica"),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} -> {self.user_id}"
