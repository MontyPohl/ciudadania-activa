from django.apps import AppConfig
from django.conf import settings


class ExperimentsAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.experiments_app"

    def ready(self) -> None:
        # Registrar señales no activa la instrumentación: cada receptor vuelve a
        # comprobar el gate S11 antes de observar o persistir nada.
        from . import celery_signals  # noqa: F401

        if getattr(settings, "S11_FAKE_AI_ENABLED", False):
            # La inyección vive en la app experimental: el runtime productivo de IA
            # conserva su frontera y nunca conoce el fake (ADR-0008).
            from apps.ai_client_app import tasks

            from .ai_runtime import get_experimental_runtime_adapter
            from .runtime import require_isolation

            require_isolation()
            tasks.get_runtime_adapter = get_experimental_runtime_adapter
