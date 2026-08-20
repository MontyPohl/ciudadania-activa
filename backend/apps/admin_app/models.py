from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.enums import ActorType, ReportPriority
from apps.core.models import BaseModel


class Institution(BaseModel):
    """Entidad pública responsable de atender incidencias (Anexo A.1).

    El MVP trabaja con tres: Municipalidad de Asunción, ANDE y ESSAP, cargadas
    por la migración de datos `admin_app.0002_seed_catalogs`.
    """

    name = models.CharField(max_length=160, unique=True)
    # Clave natural del seed idempotente: `update_or_create(slug=...)`.
    slug = models.SlugField(max_length=60, unique=True)
    contact_email = models.EmailField(blank=True)
    # Nullable porque la tesis no documenta ningún valor de SLA por institución; se
    # completará cuando exista el dato real (métricas del Sprint 13). El CHECK solo
    # actúa sobre filas con valor: en PostgreSQL una comparación con NULL satisface
    # la restricción.
    sla_hours = models.PositiveIntegerField(null=True, blank=True)
    # Baja lógica: las claves foráneas hacia el catálogo son PROTECT, de modo que una
    # institución en uso no puede borrarse y esta bandera es la única vía para
    # retirarla (RF-30). Divergencia respecto del Anexo A registrada en CT-42.
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "institutions"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(sla_hours__gt=0),
                name="institution_sla_hours_positivo",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Category(BaseModel):
    """Categoría urbana de incidencia (Anexo A.1 y A.3).

    El mapeo categoría → institución vive aquí, en `default_institution`: la tabla
    `institution_categories` de la versión original de la tesis fue eliminada
    (§33.1 del plan, contradicción 5).
    """

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    # Institución responsable por defecto (Anexo A.3). PROTECT: borrar una institución
    # no puede dejar el mapeo huérfano ni reasignarlo en silencio.
    default_institution = models.ForeignKey(
        Institution,
        on_delete=models.PROTECT,
        related_name="default_categories",
    )
    default_priority = models.CharField(
        max_length=10,
        choices=ReportPriority.choices,
        default=ReportPriority.MEDIA,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "categories"
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class InstitutionOperator(BaseModel):
    """Vínculo entre un usuario con rol `operator` y su institución (RF-05)."""

    # CASCADE: el vínculo no tiene sentido sin el usuario y no es información
    # económica ni de trazabilidad; la auditoría de su alta queda en audit_logs.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="institution_links",
    )
    institution = models.ForeignKey(
        Institution,
        on_delete=models.PROTECT,
        related_name="operators",
    )
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "institution_operators"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "institution"],
                name="institution_operator_unico_por_institucion",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.institution_id}"


class AuditLog(BaseModel):
    """Registro transversal de acciones humanas y de IA (§30 del plan, RF-49).

    Se escribe exclusivamente a través de `core.services.audit.log`. Las filas no
    se modifican ni se borran nunca: el admin las expone en modo lectura.
    """

    # SET_NULL y nullable: hay acciones sin autor humano (IA, cierre automático) y la
    # entrada de auditoría debe sobrevivir a la baja del usuario, que es justo lo que
    # RF-50 exige preservar.
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    actor_type = models.CharField(max_length=10, choices=ActorType.choices)
    action = models.CharField(max_length=120)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.actor_type}:{self.action}"
