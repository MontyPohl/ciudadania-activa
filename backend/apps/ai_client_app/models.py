from django.db import models
from django.db.models import Q

from apps.admin_app.models import Category, Institution
from apps.core.enums import ReportPriority
from apps.core.models import BaseModel
from apps.reports_app.models import Report


class AiClassification(BaseModel):
    """Resultado de la clasificación asistida por IA (Anexo A.1, RF-22).

    El Sprint 2 solo crea la tabla con los campos del Anexo A. El cliente de
    Gemini, el parser y el umbral de confianza son del Sprint 8, que podrá añadir
    columnas de forma aditiva si las necesita (decisión D2-14).
    """

    # CASCADE: la clasificación describe a un reporte concreto. Sin unicidad: el
    # Sprint 8 admite reproceso manual, de modo que un reporte puede acumular varias.
    report = models.ForeignKey(
        Report,
        on_delete=models.CASCADE,
        related_name="ai_classifications",
    )
    # Nullable: el parser estricto puede no mapear la sugerencia a un elemento del
    # catálogo, y en ese caso el reporte queda marcado para revisión humana (RF-23).
    suggested_category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ai_suggestions",
    )
    suggested_institution = models.ForeignKey(
        Institution,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ai_suggestions",
    )
    suggested_priority = models.CharField(
        max_length=10,
        choices=ReportPriority.choices,
        blank=True,
        default="",
    )
    # Decimal y no float: el valor queda auditado con exactitud (RF-24).
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    inconsistency_flag = models.BooleanField(default=False)

    class Meta:
        db_table = "ai_classifications"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(confidence__gte=0) & Q(confidence__lte=1),
                name="ai_classification_confianza_0_1",
            ),
        ]

    def __str__(self) -> str:
        return f"IA de {self.report_id}"
