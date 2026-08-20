from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "user", "notification_type", "channel", "status"]
    list_filter = ["notification_type", "channel", "status"]
    search_fields = ["user__email"]
    list_select_related = ["user"]
    raw_id_fields = ["user", "report"]
    readonly_fields = ["created_at", "updated_at"]
