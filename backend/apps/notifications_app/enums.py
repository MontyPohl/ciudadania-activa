from django.db import models


class NotificationType(models.TextChoices):
    """Plantillas de notificación previstas por el MVP.

    Cada valor corresponde a una plantilla nombrada en el plan: verificación
    aprobada/rechazada (Sprint 4), reporte creado, validación institucional,
    resuelto y rechazado (Sprint 13), canje realizado y recompensa disponible
    (Sprint 12).

    `VALIDACION_INSTITUCIONAL` designa el **evento** de validación humana, no un
    estado del reporte: no existe un estado `VALIDADO` (RF-28).
    """

    VERIFICACION_APROBADA = "VERIFICACION_APROBADA", "Verificación aprobada"
    VERIFICACION_RECHAZADA = "VERIFICACION_RECHAZADA", "Verificación rechazada"
    REPORTE_CREADO = "REPORTE_CREADO", "Reporte creado"
    VALIDACION_INSTITUCIONAL = "VALIDACION_INSTITUCIONAL", "Reporte validado"
    REPORTE_RESUELTO = "REPORTE_RESUELTO", "Reporte resuelto"
    REPORTE_RECHAZADO = "REPORTE_RECHAZADO", "Reporte rechazado"
    CANJE_REALIZADO = "CANJE_REALIZADO", "Canje realizado"
    RECOMPENSA_DISPONIBLE = "RECOMPENSA_DISPONIBLE", "Recompensa disponible"


class NotificationChannel(models.TextChoices):
    """Canales de envío. El MVP entrega solo email; push/FCM es mejora futura (CT-21)."""

    EMAIL = "EMAIL", "Email"


class NotificationStatus(models.TextChoices):
    """Estado de envío (§6.4 del plan y reintentos de los Sprints 12-13)."""

    PENDIENTE = "PENDIENTE", "Pendiente"
    ENVIADO = "ENVIADO", "Enviado"
    FALLIDO = "FALLIDO", "Fallido"
