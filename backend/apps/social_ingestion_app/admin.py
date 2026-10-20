from django.contrib import admin

from .models import XMockMessage


@admin.register(XMockMessage)
class XMockMessageAdmin(admin.ModelAdmin):
    list_display = ["simulated_at", "source_handle", "report"]
    search_fields = ["source_handle", "raw_text"]
    raw_id_fields = ["report"]
    readonly_fields = ["created_at", "updated_at"]
