from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.admin_app.models import Category, Institution
from apps.core.enums import RejectionReason, ReportPriority, ReportSource, ReportStatus
from apps.core.models import BaseModel
from apps.reports_app.enums import MediaType


class Report(BaseModel):
    """Reporte urbano georreferenciado (Anexo A.1, A.4, A.5).

    El Sprint 2 crea la estructura; la creación real de reportes es del Sprint 6 y
    la validación institucional del Sprint 9.
    """

    # Nullable (CT-19 / O-03): los reportes ingestados desde Telegram o X simulado no
    # tienen ciudadano verificado. SET_NULL conserva el reporte —dato público de
    # transparencia— si el autor se da de baja.
    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    # Obligatoria: el ciudadano elige la categoría en el primer paso del asistente de
    # creación (CU-03, RF-08) y la consulta previa de duplicados la exige como
    # parámetro. La IA puede reemplazar el valor en el Sprint 8, no crearlo.
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="reports",
    )
    # Nullable (D2-16): la institución responsable definitiva se fija al validar
    # (Sprint 9). El Sprint 6 podrá prellenarla desde `category.default_institution`,
    # pero el esquema no la exige y no se admiten instituciones ficticias de relleno.
    institution = models.ForeignKey(
        Institution,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reports",
    )
    description = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.CREADO,
    )
    priority = models.CharField(
        max_length=10,
        choices=ReportPriority.choices,
        default=ReportPriority.MEDIA,
    )
    # Obligatorio (CT-19): sostiene la regla "las fuentes no verificadas no generan
    # puntos" (RF-45).
    source = models.CharField(
        max_length=20,
        choices=ReportSource.choices,
        default=ReportSource.PLATFORM,
    )
    # geometry(Point, 4326) con índice GIST, que GeoDjango crea por defecto
    # (spatial_index=True). Se mantiene geometry y no geography (D2-07): las consultas
    # en metros del Sprint 6 resolverán el cast a geography o ST_DistanceSphere.
    location = gis_models.PointField(srid=4326)
    # La validación institucional es un evento auditable, no un estado (RF-28).
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="validated_reports",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_cycle = models.PositiveIntegerField(default=0)
    # Motivo tipificado del rechazo (RF-14). Vacío mientras el reporte no esté
    # rechazado; `report_status_history.reason` conserva la nota libre del operador.
    rejection_reason = models.CharField(
        max_length=30,
        choices=RejectionReason.choices,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "reports"
        ordering = ["-created_at"]
        indexes = [
            # B-tree exigidos por el Anexo A.5. El de `institution_id` lo crea Django
            # con la clave foránea, así que declararlo aquí sería un índice duplicado.
            models.Index(fields=["status"], name="report_status_idx"),
            models.Index(fields=["priority"], name="report_priority_idx"),
            models.Index(fields=["created_at"], name="report_created_at_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(resolution_cycle__gte=0),
                name="report_resolution_cycle_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.status} · {self.category_id}"


class ReportMedia(BaseModel):
    """Fotografía o archivo adjunto de un reporte (Anexo A.1, RF-12)."""

    # CASCADE: un archivo sin reporte no significa nada. En la práctica un reporte con
    # historia económica no puede borrarse: el ledger lo protege con PROTECT.
    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="media",
    )
    # Nombre legado: para fotos privadas de reportes contiene un object key de
    # Storage (`reports/<report>/<media>.ext`), nunca una URL pública o firmada.
    file_url = models.TextField()
    # SHA-256 del archivo (RF-12). Sin índice todavía: la consulta de detección de
    # archivo idéntico se implementa en el Sprint 6.
    file_hash = models.CharField(max_length=64)
    media_type = models.CharField(
        max_length=10,
        choices=MediaType.choices,
        default=MediaType.FOTO,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_media",
    )

    class Meta:
        db_table = "report_media"
        verbose_name_plural = "report media"
        constraints = [
            models.UniqueConstraint(
                fields=["file_hash"],
                condition=models.Q(media_type=MediaType.FOTO),
                name="report_media_foto_hash_unico",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.media_type} de {self.report_id}"


class ReportStatusHistory(BaseModel):
    """Historial completo de transiciones de estado (RF-11)."""

    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    # Vacío en el asiento inicial del reporte, que no tiene estado previo.
    previous_status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        blank=True,
        default="",
    )
    new_status = models.CharField(max_length=20, choices=ReportStatus.choices)
    # Nullable: el cierre automático RESUELTO -> CERRADO a las 72 h no tiene actor
    # humano (RF-16).
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="status_changes",
    )
    reason = models.TextField(blank=True)
    resolution_cycle = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "report_status_history"
        ordering = ["created_at"]
        verbose_name_plural = "report status history"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(resolution_cycle__gte=0),
                name="report_history_cycle_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.previous_status or '-'} -> {self.new_status}"


class ResolutionEvidence(BaseModel):
    """Evidencia de resolución cargada por el operador (RF-15, CU-07)."""

    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="resolution_evidences",
    )
    description = models.TextField()
    evidence_url = models.TextField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_evidences",
    )
    resolution_cycle = models.PositiveIntegerField()

    class Meta:
        db_table = "resolution_evidences"
        verbose_name_plural = "resolution evidences"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(resolution_cycle__gte=1),
                name="resolution_evidence_cycle_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"Evidencia de {self.report_id}"


class ReportConfirmation(BaseModel):
    """Confirmación ciudadana única de un reporte activo (RF-18 a RF-21).

    `created_at` del modelo base **es** el instante de la confirmación: no se
    duplica con una columna `confirmed_at` (decisión D2-10). El serializador del
    Sprint 7 podrá exponerlo con ese nombre.

    No existe ningún `CHECK` que impida confirmar el propio reporte: la regla
    compara con otra tabla y PostgreSQL no puede expresarla sin un trigger (C-02,
    CT-16). Vive en el servicio de dominio del Sprint 7.
    """

    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="confirmations",
    )
    # PROTECT: la confirmación es un acto civil atribuido que alimenta el ledger y no
    # debe perder a su autor.
    citizen = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmations",
    )

    class Meta:
        db_table = "report_confirmations"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "citizen"],
                name="report_confirmation_unica_por_ciudadano",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.citizen_id} confirma {self.report_id}"
