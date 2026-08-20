from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-secret-key"  # noqa: S105
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True

# La imagen `postgis/postgis` habilita PostGIS en la base del proyecto y en
# `template_postgis`, pero no en `template1`, que es la plantilla que PostgreSQL usa por
# defecto al crear la base de pruebas. Sin este ajuste, `migrate` sobre la base de test
# falla al crear `reports.location` porque el tipo `geometry` no existe allí.
# Se declara solo en los ajustes de prueba: la extensión sigue siendo infraestructura
# provisionada (ADR-0003) y ninguna migración la crea. Local y CI usan la misma imagen.
DATABASES["default"]["TEST"] = {"TEMPLATE": "template_postgis"}  # noqa: F405
