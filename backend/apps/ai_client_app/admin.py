from django.contrib import admin

from apps.core.admin_mixins import ReadOnlyAdminMixin

from .models import AiClassification


@admin.register(AiClassification)
class AiClassificationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # Posterior a S11. Lo que sugirió la IA es evidencia: solo se inspecciona.
    list_display = [
        "created_at",
        "report",
        "suggested_category",
        "suggested_institution",
        "suggested_priority",
        "confidence",
        "inconsistency_flag",
    ]
    list_filter = ["inconsistency_flag", "suggested_priority", "suggested_institution"]
    list_select_related = ["suggested_category", "suggested_institution"]
    raw_id_fields = ["report"]
