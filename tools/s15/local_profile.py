"""Guards del perfil de medición S15; no importa Django ni lee archivos .env."""

from urllib.parse import urlsplit


class ProfileError(ValueError):
    """El destino no corresponde a los recursos sintéticos de S15."""


def validate_environment(values):
    exact = {
        "S15_LOCAL_PROFILE": "isolated-measurement-v1",
        "DJANGO_SETTINGS_MODULE": "tools.s15.measurement_settings",
        "DATABASE_URL": "postgis://ciudadania:s15-local-database-only@ca-s15-db:5432/ciudadania_s15",
        "REDIS_URL": "redis://ca-s15-redis:6379/0",
        "SUPABASE_URL": "http://127.0.0.1:18515",
        "SUPABASE_STORAGE_BUCKET_PRIVATE": "s15-private",
        "SUPABASE_STORAGE_BUCKET_REPORTS": "s15-reports",
        "AI_CLASSIFICATION_ENABLED": "false",
        "AI_PROVIDER": "",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "GOOGLE_OAUTH_CLIENT_ID": "",
        "S11_EXPERIMENTS_ENABLED": "false",
        "S11_LOCAL_STORAGE_ENABLED": "false",
        "BREVO_API_URL": "http://127.0.0.1:18516/v3/smtp/email",
    }
    for key, expected in exact.items():
        if values.get(key) != expected:
            raise ProfileError(f"Configuración local incompatible: {key}.")
    for key in ("DJANGO_SECRET_KEY", "DOCUMENT_HMAC_SECRET", "SUPABASE_SERVICE_ROLE_KEY"):
        value = values.get(key, "")
        if not value.startswith("s15-synthetic-") or len(value) < 32:
            raise ProfileError(f"Se requiere una clave sintética S15: {key}.")
    mail_key = values.get("BREVO_API_KEY", "")
    if mail_key and not mail_key.startswith("s15-synthetic-"):
        raise ProfileError("La clave de correo debe ser sintética o vacía.")
    hosts = values.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if not hosts or not set(hosts) <= {"localhost", "127.0.0.1", "testserver"}:
        raise ProfileError("Solo se admiten hosts de loopback/pruebas.")
    # Evitar overrides que desacoplen web, caché, broker y resultados.
    for key in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        if key in values and values[key] != exact["REDIS_URL"]:
            raise ProfileError(f"Override no aislado: {key}.")
    return {
        "profile": exact["S15_LOCAL_PROFILE"],
        "database": urlsplit(values["DATABASE_URL"]).path.removeprefix("/"),
        "storage": exact["SUPABASE_URL"],
        "mail": "local-substitute" if mail_key else "disabled",
    }
