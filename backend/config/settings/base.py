from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BASE_DIR.parent

env = environ.Env()
environ.Env.read_env(str(REPO_ROOT / ".env"))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

AUTH_USER_MODEL = "auth_app.User"
API_VERSION = env("API_VERSION", default="0.1.0")

# Client ID de Google. No es un secreto: es el `audience` que debe llevar el ID token y
# viaja al navegador como NEXT_PUBLIC_GOOGLE_CLIENT_ID. Con el valor vacío el backend
# arranca con normalidad, pero cualquier intento real de login falla de forma segura:
# `auth_app.services.google` se niega a verificar tokens sin `audience` configurado.
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.admin",
    "django.contrib.gis",
    "rest_framework",
    "corsheaders",
    "apps.core",
    "apps.auth_app",
    "apps.reports_app",
    "apps.rewards_app",
    "apps.map_app",
    "apps.analytics_app",
    "apps.notifications_app",
    "apps.admin_app",
    "apps.ai_client_app",
    "apps.social_ingestion_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgis://ciudadania:ciudadania@localhost:5432/ciudadania_activa",
    ),
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

# PostGIS no reside en el mismo esquema en todos los entornos: en Supabase está instalada
# en `gis` y en el contenedor local de infra/docker-compose.yml en `public` (ADR-0003).
# Sin `gis` en el search_path, GeoDjango no resuelve el tipo `geometry` ni funciones como
# postgis_lib_version(), y `connection.ops.spatial_version` falla con ImproperlyConfigured.
# Se fija de forma explícita para que ambos entornos se comporten igual; PostgreSQL ignora
# sin error los esquemas de la lista que no existan.
DB_SEARCH_PATH = env("DB_SEARCH_PATH", default="public,gis,extensions")
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["options"] = f"-c search_path={DB_SEARCH_PATH}"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Asuncion"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    # Denegación por defecto (Sprint 3): una vista que olvide declarar su permiso queda
    # cerrada, no abierta a cualquier autenticado. Toda vista debe declararlo de forma
    # explícita: AllowAny, IsAuthenticated, IsVerifiedCitizen, IsAdmin, etc.
    "DEFAULT_PERMISSION_CLASSES": ["apps.core.permissions.DenyAll"],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# JWT propio del sistema, emitido tras verificar el ID token de Google (ADR-0005).
# `SIGNING_KEY` no se declara: SimpleJWT usa `SECRET_KEY` por defecto, de modo que no se
# introduce un secreto nuevo. Consecuencia asumida: rotar DJANGO_SECRET_KEY invalida las
# sesiones vigentes.
# `token_blacklist` NO se instala: crearía dos tablas y el Sprint 3 no introduce ninguna.
# Por eso tampoco se rota el refresh: sin blacklist, rotar no revocaría el anterior y daría
# una falsa sensación de revocación. El Sprint 3 no implementa revocación server-side.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=15)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ALGORITHM": "HS256",
    # La clave primaria del usuario es UUID; SimpleJWT la serializa como cadena.
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    # `last_login` se actualiza solo en el login con Google, nunca al renovar el access.
    "UPDATE_LAST_LOGIN": False,
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Fijado en el Sprint 1: PostgreSQL/PostGIS reside en Supabase y se conecta siempre
# mediante DATABASE_URL. Ninguna rama de código distingue entornos; lo único que varía
# es el valor de DATABASE_URL (y, si hiciera falta, DB_SEARCH_PATH). Ver ADR-0003.
# El aislamiento entre la base local y Supabase se resuelve en infra/docker-compose.yml,
# que fija deliberadamente DATABASE_URL al contenedor local, no aquí.
