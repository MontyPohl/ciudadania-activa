from django.contrib import admin
from django.contrib.gis import admin as gis_admin

from apps.core.admin_mixins import ReadOnlyAdminMixin

from .models import (
    Report,
    ReportConfirmation,
    ReportMedia,
    ReportStatusHistory,
    ResolutionEvidence,
)


@admin.register(Report)
class ReportAdmin(gis_admin.GISModelAdmin):
    list_display = [
        "created_at",
        "status",
        "priority",
        "source",
        "category",
        "institution",
        "citizen",
        "validated_at",
        "resolved_at",
    ]
    list_filter = ["status", "priority", "source", "category", "institution"]
    search_fields = ["description", "citizen__email"]
    list_select_related = ["category", "institution", "citizen"]
    raw_id_fields = ["citizen", "validated_by"]
    date_hierarchy = "created_at"
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ReportMedia)
class ReportMediaAdmin(admin.ModelAdmin):
    list_display = ["created_at", "report", "media_type", "file_hash"]
    list_filter = ["media_type"]
    search_fields = ["file_hash"]
    raw_id_fields = ["report", "uploaded_by"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ReportStatusHistory)
class ReportStatusHistoryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["created_at", "report", "previous_status", "new_status", "changed_by"]
    list_filter = ["new_status", "previous_status"]
    raw_id_fields = ["report", "changed_by"]


@admin.register(ResolutionEvidence)
class ResolutionEvidenceAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["created_at", "report", "uploaded_by"]
    raw_id_fields = ["report", "uploaded_by"]


@admin.register(ReportConfirmation)
class ReportConfirmationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # `created_at` es el instante de la confirmación (D2-10): no hay columna
    # `confirmed_at` que mostrar.
    list_display = ["created_at", "report", "citizen"]
    search_fields = ["citizen__email"]
    list_select_related = ["citizen"]
    raw_id_fields = ["report", "citizen"]
