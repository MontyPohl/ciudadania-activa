from django.apps import AppConfig


class SocialIngestionAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.social_ingestion_app"
    label = "social_ingestion_app"
    verbose_name = "Social Ingestion"
