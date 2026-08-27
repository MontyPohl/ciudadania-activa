from .base import *  # noqa: F403

DEBUG = False
# 32 bytes como mínimo: PyJWT advierte con claves más cortas al firmar HS256, y los
# JWT del Sprint 3 se firman con SECRET_KEY.
SECRET_KEY = "clave-de-prueba-solo-para-la-suite-de-tests"  # noqa: S105
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True

# La suite nunca usa el Redis compartido con Celery. Además de hacer las pruebas
# deterministas, esto impide que un ``clear`` legado pueda convertirse en ``FLUSHDB``.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ciudadania-activa-tests",
    }
}

# El envío del Sprint 4 no pasa por `django.core.mail`, sino por la API HTTPS de Brevo,
# y la fixture `brevo_api` dobla ese cliente en toda la suite. El backend `locmem` se
# conserva igualmente como red de seguridad: si algún día una ruta usara `send_mail`, la
# suite tampoco abriría una conexión SMTP.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Valores ficticios y **reconocibles**: varias pruebas comprueban que este literal no
# aparece en los logs. La suite nunca contacta con Brevo: `BREVO_API_URL` apunta a un
# host inexistente a propósito, de modo que un doble mal puesto se manifiesta como
# error de red y no como un envío real.
BREVO_API_KEY = "clave-brevo-de-prueba-no-real"  # noqa: S105
BREVO_API_URL = "https://brevo.invalido.test/v3/smtp/email"
DEFAULT_FROM_EMAIL = "Ciudadanía Activa <remitente-de-prueba@example.test>"

# La imagen `postgis/postgis` habilita PostGIS en la base del proyecto y en
# `template_postgis`, pero no en `template1`, que es la plantilla que PostgreSQL usa por
# defecto al crear la base de pruebas. Sin este ajuste, `migrate` sobre la base de test
# falla al crear `reports.location` porque el tipo `geometry` no existe allí.
# Se declara solo en los ajustes de prueba: la extensión sigue siendo infraestructura
# provisionada (ADR-0003) y ninguna migración la crea. Local y CI usan la misma imagen.
DATABASES["default"]["TEST"] = {"TEMPLATE": "template_postgis"}  # noqa: F405
