from django.contrib import admin

from .models import AiClassification


@admin.register(AiClassification)
class AiClassificationAdmin(admin.ModelAdmin):
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
    readonly_fields = [
        "created_at",
        "updated_at",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    ]
