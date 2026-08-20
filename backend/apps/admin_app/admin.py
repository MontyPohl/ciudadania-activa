from django.contrib import admin

from apps.core.admin_mixins import ReadOnlyAdminMixin

from .models import AuditLog, Category, Institution, InstitutionOperator


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "contact_email", "sla_hours", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "default_institution", "default_priority", "is_active"]
    list_filter = ["default_institution", "default_priority", "is_active"]
    search_fields = ["name", "slug"]
    list_select_related = ["default_institution"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["created_at", "updated_at"]


@admin.register(InstitutionOperator)
class InstitutionOperatorAdmin(admin.ModelAdmin):
    list_display = ["user", "institution", "active"]
    list_filter = ["institution", "active"]
    search_fields = ["user__email", "institution__name"]
    list_select_related = ["user", "institution"]
    raw_id_fields = ["user"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Solo lectura: las entradas de auditoría no se crean, editan ni borran a mano.

    La única vía de escritura es `core.services.audit.log` (§30 del plan).
    """

    list_display = ["created_at", "actor_type", "actor_user", "action"]
    list_filter = ["actor_type", "action"]
    search_fields = ["action", "actor_user__email"]
    list_select_related = ["actor_user"]
    date_hierarchy = "created_at"
