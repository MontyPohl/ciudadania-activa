from django.db import models


class ReportStatus(models.TextChoices):
    """Estados del reporte (Anexo A.2).

    No existe un estado `VALIDADO`: la validación institucional es un **evento**
    humano que se registra en `reports.validated_at` / `validated_by`, no una
    transición de estado (RF-28, §33.1 del plan).
    """

    CREADO = "CREADO", "Creado"
    CLASIFICADO = "CLASIFICADO", "Clasificado"
    ASIGNADO = "ASIGNADO", "Asignado"
    EN_PROCESO = "EN_PROCESO", "En proceso"
    RESUELTO = "RESUELTO", "Resuelto"
    CERRADO = "CERRADO", "Cerrado"
    RECHAZADO = "RECHAZADO", "Rechazado"


class ReportPriority(models.TextChoices):
    """Prioridades del reporte (Anexo A.2)."""

    BAJA = "BAJA", "Baja"
    MEDIA = "MEDIA", "Media"
    ALTA = "ALTA", "Alta"


class ReportSource(models.TextChoices):
    """Origen del reporte (RF-17, CT-19).

    Sostiene la regla "las fuentes no verificadas no generan puntos" (RF-45).
    """

    PLATFORM = "platform", "Plataforma"
    TELEGRAM = "telegram", "Telegram"
    X_MOCK = "x_mock", "X simulado"


class RejectionReason(models.TextChoices):
    """Motivos tipificados de rechazo (RF-14, Anexo A.2)."""

    FUERA_DE_COMPETENCIA = "fuera_de_competencia", "Fuera de competencia"
    DUPLICADO = "duplicado", "Duplicado"
    INFORMACION_INSUFICIENTE = "informacion_insuficiente", "Información insuficiente"
    CONTENIDO_INAPROPIADO = "contenido_inapropiado", "Contenido inapropiado"


class ActorType(models.TextChoices):
    """Tipo de actor de una entrada de auditoría (§30 del plan, RF-24)."""

    HUMAN = "human", "Humano"
    AI = "ai", "IA"
    SYSTEM = "system", "Sistema"
