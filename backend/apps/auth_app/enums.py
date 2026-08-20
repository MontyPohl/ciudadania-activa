from django.db import models


class UserRole(models.TextChoices):
    """Roles del sistema (RF-05). Los permisos se aplican en el Sprint 3."""

    CITIZEN = "citizen", "Ciudadano"
    OPERATOR = "operator", "Operador institucional"
    ADMIN = "admin", "Administrador"


class VerificationStatus(models.TextChoices):
    """Estados de verificación de identidad (RF-03, Anexo A.2).

    La aprobación es una revisión humana administrativa (Sprint 4).
    """

    PENDIENTE = "PENDIENTE", "Pendiente"
    APROBADO = "APROBADO", "Aprobado"
    RECHAZADO = "RECHAZADO", "Rechazado"


class RankingVisibility(models.TextChoices):
    """Preferencia de visibilidad en el ranking (RF-07, Anexo A.2)."""

    FULL_NAME = "FULL_NAME", "Nombre completo"
    FIRST_INITIAL = "FIRST_INITIAL", "Nombre e inicial"
    ALIAS = "ALIAS", "Alias"
    PRIVATE = "PRIVATE", "Usuario privado"
