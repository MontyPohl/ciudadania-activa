from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.admin_app.models import Institution
from apps.core.models import BaseModel
from apps.reports_app.models import Report, ReportConfirmation
from apps.rewards_app.enums import RewardEventType, RewardTxStatus


class Reward(BaseModel):
    """Recompensa institucional simulada (Anexo A.1/A.6, RF-43)."""

    institution = models.ForeignKey(
        Institution,
        on_delete=models.PROTECT,
        related_name="rewards",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    # Costo fijo del MVP (D-02). El campo se conserva estructuralmente normal con
    # DEFAULT 1000 por extensibilidad futura; la prohibición de editarlo es de la capa
    # de aplicación: readonly en el admin ahora y serializador/servicio en el Sprint 12.
    credit_cost = models.PositiveIntegerField(default=1000)
    active = models.BooleanField(default=True)
    simulated = models.BooleanField(default=True)

    class Meta:
        db_table = "rewards"
        ordering = ["institution__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["institution", "name"],
                name="reward_unica_por_institucion",
            ),
            models.CheckConstraint(
                condition=Q(credit_cost__gt=0),
                name="reward_credit_cost_positivo",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.institution_id})"


class RewardTransaction(BaseModel):
    """Ledger de puntos de impacto y créditos institucionales (§6.5, ADR-0004).

    Los valores económicos son inmutables: `impact_delta` y `credit_delta` se
    fijan al insertar la fila y no se reescriben nunca. Lo que cambia después es
    el estado, la institución y las marcas temporales (C-07). Los saldos **no** se
    almacenan: se derivan por agregación filtrando `status='ACREDITADO'`.

    El Sprint 2 crea la tabla; ninguna fila se escribe desde aquí. La escritura es
    exclusiva de los servicios de dominio de los Sprints 6 a 12, siempre síncrona
    y dentro de la transacción del llamador.
    """

    # PROTECT en todas las claves foráneas: el ledger nunca puede perder el sujeto,
    # el origen ni la institución de un movimiento económico (RF-50).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reward_transactions",
    )
    report = models.ForeignKey(
        Report,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reward_transactions",
    )
    confirmation = models.ForeignKey(
        ReportConfirmation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reward_transactions",
    )
    # Nullable por diseño: REPORT_CREATED y CONFIRMATION_CREATED nacen PENDIENTE sin
    # institución, que se fija al acreditar con la institución validante (RF-35/RF-38).
    institution = models.ForeignKey(
        Institution,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reward_transactions",
    )
    event_type = models.CharField(max_length=40, choices=RewardEventType.choices)
    impact_delta = models.IntegerField()
    credit_delta = models.IntegerField()
    status = models.CharField(max_length=20, choices=RewardTxStatus.choices)
    # Enlaza una fila REVERSAL con la transacción original que revierte.
    related_transaction = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversals",
    )
    credited_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    resolution_cycle = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "reward_transactions"
        ordering = ["-created_at"]
        indexes = [
            # Saldos por institución y panel del ciudadano.
            models.Index(
                fields=["user", "institution", "status", "created_at"],
                name="reward_tx_saldo_idx",
            ),
            # Top 10 del mes en curso, que imputa el mes por `credited_at` (O-08).
            models.Index(fields=["status", "credited_at"], name="reward_tx_ranking_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(resolution_cycle__isnull=True) | Q(resolution_cycle__gte=0),
                name="reward_tx_cycle_nonnegative_or_null",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(
                        event_type__in=[
                            RewardEventType.VALIDATION_BONUS,
                            RewardEventType.RESOLUTION_BONUS,
                        ]
                    )
                    | (
                        Q(report__isnull=False)
                        & Q(resolution_cycle__isnull=False)
                        & Q(resolution_cycle__gte=1)
                    )
                ),
                name="reward_tx_bonus_cycle_required",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(event_type=RewardEventType.REVERSAL)
                    | (
                        Q(report__isnull=False)
                        & Q(resolution_cycle__isnull=False)
                        & Q(resolution_cycle__gte=1)
                        & Q(related_transaction__isnull=False)
                    )
                ),
                name="reward_tx_reversal_fields_required",
            ),
            models.CheckConstraint(
                condition=(
                    Q(related_transaction__isnull=True) | Q(event_type=RewardEventType.REVERSAL)
                ),
                name="reward_tx_related_only_reversal",
            ),
            # Coherencia estado <-> marca temporal (D2-08). REVERTIDO no exige que
            # `credited_at` sea NULL: la fila original acreditada conserva su
            # `credited_at` como rastro, y una fila REVERSAL puede nacer REVERTIDO sin
            # haber estado nunca acreditada.
            models.CheckConstraint(
                condition=(
                    (
                        Q(status=RewardTxStatus.PENDIENTE)
                        & Q(credited_at__isnull=True)
                        & Q(voided_at__isnull=True)
                        & Q(reversed_at__isnull=True)
                    )
                    | (
                        Q(status=RewardTxStatus.ACREDITADO)
                        & Q(credited_at__isnull=False)
                        & Q(voided_at__isnull=True)
                        & Q(reversed_at__isnull=True)
                    )
                    | (
                        Q(status=RewardTxStatus.ANULADO)
                        & Q(credited_at__isnull=True)
                        & Q(voided_at__isnull=False)
                        & Q(reversed_at__isnull=True)
                    )
                    | (
                        Q(status=RewardTxStatus.REVERTIDO)
                        & Q(reversed_at__isnull=False)
                        & Q(voided_at__isnull=True)
                    )
                ),
                name="reward_tx_status_timestamps_coherentes",
            ),
            # Índices únicos parciales (D2-09): garantizan en base de datos que la
            # creación de un reporte y cada confirmación producen exactamente una fila.
            # No se extienden a los demás event_type: tras una reversión, una
            # revalidación legítima genera un segundo VALIDATION_BONUS.
            models.UniqueConstraint(
                fields=["report"],
                condition=Q(event_type=RewardEventType.REPORT_CREATED),
                name="reward_tx_report_created_unico",
            ),
            models.UniqueConstraint(
                fields=["confirmation"],
                condition=Q(event_type=RewardEventType.CONFIRMATION_CREATED),
                name="reward_tx_confirmation_created_unico",
            ),
            models.UniqueConstraint(
                fields=["report", "resolution_cycle"],
                condition=Q(event_type=RewardEventType.VALIDATION_BONUS),
                name="reward_tx_validation_bonus_cycle_unique",
            ),
            models.UniqueConstraint(
                fields=["report", "resolution_cycle"],
                condition=Q(event_type=RewardEventType.RESOLUTION_BONUS),
                name="reward_tx_resolution_bonus_cycle_unique",
            ),
            models.UniqueConstraint(
                fields=["related_transaction"],
                condition=Q(event_type=RewardEventType.REVERSAL),
                name="reward_tx_reversal_original_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.impact_delta:+d}/{self.credit_delta:+d} {self.status}"


class RewardRedemption(BaseModel):
    """Canje de una recompensa por créditos institucionales (Anexo A.1/A.6, RF-43).

    El vale emitido conserva su código y su validez incluso si una reversión
    posterior deja el saldo institucional negativo (D-01). El servicio de canje y
    el límite de uno por institución, usuario y mes son del Sprint 12.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    reward = models.ForeignKey(
        Reward,
        on_delete=models.PROTECT,
        related_name="redemptions",
    )
    code = models.CharField(max_length=80, unique=True)
    credits_spent = models.PositiveIntegerField(default=1000)

    class Meta:
        db_table = "reward_redemptions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "reward", "created_at"],
                name="reward_redemption_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(credits_spent__gt=0),
                name="redemption_credits_positivo",
            ),
        ]

    def __str__(self) -> str:
        return self.code
