from django.contrib import admin

from apps.core.admin_mixins import ReadOnlyAdminMixin

from .models import Reward, RewardRedemption, RewardTransaction


@admin.register(RewardTransaction)
class RewardTransactionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Ledger en modo lectura estricta.

    Los importes son inmutables y el estado solo puede cambiarlo un servicio de
    dominio dentro de una transacción (ADR-0004). Un formulario de admin editable
    permitiría violar esa semántica, así que se niegan alta, cambio y borrado; la
    inspección exigida por el Sprint 2 se cubre con el permiso de vista.
    """

    list_display = [
        "created_at",
        "user",
        "event_type",
        "impact_delta",
        "credit_delta",
        "status",
        "institution",
        "credited_at",
        "voided_at",
        "reversed_at",
    ]
    list_filter = ["status", "event_type", "institution"]
    search_fields = ["user__email"]
    list_select_related = ["user", "institution", "report", "confirmation"]
    date_hierarchy = "created_at"


@admin.register(Reward)
class RewardAdmin(admin.ModelAdmin):
    list_display = ["name", "institution", "credit_cost", "active", "simulated"]
    list_filter = ["institution", "active", "simulated"]
    search_fields = ["name"]
    list_select_related = ["institution"]
    # El costo es fijo en 1.000 créditos durante el MVP y el operador no puede
    # modificarlo (D-02). Se protege aquí, no con `editable=False` en el modelo: el
    # campo debe seguir siendo estructuralmente normal para la extensibilidad futura.
    readonly_fields = ["credit_cost", "created_at", "updated_at"]


@admin.register(RewardRedemption)
class RewardRedemptionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Solo lectura: un canje es un hecho económico con vale ya emitido (D-01)."""

    list_display = [
        "created_at",
        "user",
        "reward",
        "institution",
        "period_month",
        "code",
        "credits_spent",
    ]
    list_filter = ["reward", "institution", "period_month"]
    search_fields = ["code", "user__email"]
    list_select_related = ["user", "reward", "institution"]
    date_hierarchy = "created_at"
