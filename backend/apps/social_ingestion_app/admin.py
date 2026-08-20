from django.contrib import admin

from .models import TelegramIngestedMessage, XMockMessage


@admin.register(TelegramIngestedMessage)
class TelegramIngestedMessageAdmin(admin.ModelAdmin):
    list_display = ["created_at", "telegram_chat_id", "sender_alias", "report"]
    search_fields = ["telegram_chat_id", "sender_alias", "raw_text"]
    raw_id_fields = ["report"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(XMockMessage)
class XMockMessageAdmin(admin.ModelAdmin):
    list_display = ["simulated_at", "source_handle", "report"]
    search_fields = ["source_handle", "raw_text"]
    raw_id_fields = ["report"]
    readonly_fields = ["created_at", "updated_at"]
