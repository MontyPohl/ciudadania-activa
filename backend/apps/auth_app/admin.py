from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import CitizenProfile, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["email"]
    list_display = [
        "email",
        "full_name",
        "role",
        "verification_status",
        "is_staff",
        "is_active",
        "date_joined",
    ]
    list_filter = ["role", "verification_status", "is_staff", "is_active"]
    search_fields = ["email", "full_name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos personales", {"fields": ("full_name",)}),
        ("Dominio", {"fields": ("role", "verification_status", "google_oauth_id")}),
        (
            "Permisos",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"fields": ("email", "password1", "password2")}),)


@admin.register(CitizenProfile)
class CitizenProfileAdmin(admin.ModelAdmin):
    """Inspección del perfil sin exponer los datos sensibles de verificación.

    `document_hmac` y `verification_selfie_url` quedan fuera de `list_display` y de
    `search_fields` —buscar por HMAC permitiría confirmar la cédula de una persona
    probando valores— y son de solo lectura en el detalle (§12, Seguridad).
    """

    list_display = ["user", "ranking_visibility", "verified_at", "verified_by"]
    list_filter = ["ranking_visibility"]
    search_fields = ["user__email"]
    list_select_related = ["user", "verified_by"]
    raw_id_fields = ["user", "verified_by"]
    readonly_fields = [
        "document_hmac",
        "verification_selfie_url",
        "created_at",
        "updated_at",
    ]
