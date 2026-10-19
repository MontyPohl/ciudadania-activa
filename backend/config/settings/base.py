from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import environ
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

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

# --- Verificación de identidad (Sprint 4) ---------------------------------------
#
# Todos con default vacío **a propósito**: `prod.py` no añade ningún RuntimeError por
# ellos. Backend web y worker de Celery comparten estos ajustes, y el worker no
# necesita ni la clave HMAC ni la service-role de Storage; exigirlas para arrancar
# obligaría a copiar credenciales privilegiadas a un servicio que no las usa.
#
# El comportamiento con configuración ausente es **fallo cerrado local**: el servicio
# de identidad y el cliente de Storage levantan su excepción de configuración y la
# vista responde 503 genérico. Ninguna otra parte del sistema se ve afectada. Que
# estén presentes en el backend web se verifica en el gate de despliegue.

# Clave del HMAC-SHA-256 del número de cédula. **Cada entorno tiene la suya**:
# desarrollo la suya, la suite de pruebas una ficticia y producción la real, que no se
# comparte con desarrollo ni con CI. La de producción debe permanecer **estable** una
# vez exista la primera identidad verificada real: cambiarla invalida todos los
# `document_hmac` ya calculados y no hay forma de recalcularlos, porque el número en
# claro no se persiste en ninguna parte. Ver ADR-0006.
DOCUMENT_HMAC_SECRET = env("DOCUMENT_HMAC_SECRET", default="")

# Supabase Storage. La service-role es una credencial con privilegios totales sobre el
# almacenamiento: solo se configura en el backend web, nunca en el worker ni en Vercel.
SUPABASE_URL = env("SUPABASE_URL", default="")
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", default="")
SUPABASE_STORAGE_BUCKET_PRIVATE = env("SUPABASE_STORAGE_BUCKET_PRIVATE", default="")
SUPABASE_STORAGE_BUCKET_REPORTS = env("SUPABASE_STORAGE_BUCKET_REPORTS", default="")

# Límite funcional de la selfie, comprobado sobre `UploadedFile.size`. No se confunde
# con FILE_UPLOAD_MAX_MEMORY_SIZE, que solo decide memoria frente a archivo temporal,
# ni con DATA_UPLOAD_MAX_MEMORY_SIZE, que no aplica a campos de archivo en multipart.
IDENTITY_SELFIE_MAX_BYTES = 5 * 1024 * 1024

# Vida de la URL firmada con la que un administrador ve la selfie. No se persiste.
IDENTITY_SIGNED_URL_TTL = 300

# --- Email transaccional (notifications_app, Sprint 4) --------------------------
#
# Configuración del **worker de Celery**, que es quien envía. El backend web solo crea
# la fila en `notifications`, así que no necesita la clave de Brevo y `prod.py` no la
# exige para arrancar: sin ella el envío falla cerrado y la notificación queda en
# FALLIDO, que es un estado previsto y consultable, no una caída del servicio.
#
# **No se usa SMTP.** El plan Free de Railway no ofrece SMTP saliente: la conexión queda
# colgada hasta el timeout, que es exactamente lo que se observó en el E2E productivo del
# 23/08/2026 —TimeoutError tras 135 s—. La entrega se hace contra la API HTTPS de correo
# transaccional de Brevo, que sale por el 443 como cualquier otra llamada del proyecto.
# Ver ADR-0015.
#
# `BREVO_API_KEY` es una **clave de API v3**, no la clave SMTP: son credenciales
# distintas del mismo proveedor y la SMTP no autentica contra esta API.
# --- Clasificación asistida por IA (Sprint 8) ---
# Interruptor deliberado y separado de la credencial. Con la clave presente pero el
# flag apagado no se clasifica nada: sin él, una clave de proveedor olvidada en un
# entorno bastaría para que un despliegue empezara a clasificar reportes reales.
AI_CLASSIFICATION_ENABLED = env.bool("AI_CLASSIFICATION_ENABLED", default=False)
# v1 = prompt de EX-09; v2 = criterios de prioridad post-S12.
AI_PROMPT_VERSION = env("AI_PROMPT_VERSION", default="v1")
# Proveedor que clasifica: `openai` o `gemini` (ADR-0016). Sin valor por defecto:
# vacío o desconocido equivale a no clasificar, aunque haya claves presentes.
AI_PROVIDER = env("AI_PROVIDER", default="")
# Secretos del backend y del worker. Nunca se registran, no viajan en respuestas y
# no se auditan (contrato S8-A, D14).
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
# Sin valor por defecto a propósito: el modelo se documenta junto a la medición de
# concordancia (CT-32, O-05) y fijar aquí uno concreto lo escondería.
OPENAI_MODEL = env("OPENAI_MODEL", default="")
# `reasoning.effort` de la Responses API. Vacío omite el parámetro y deja el valor
# por defecto del modelo; forma parte de lo que se documenta con la medición.
OPENAI_REASONING_EFFORT = env("OPENAI_REASONING_EFFORT", default="")
# Proveedor histórico de S8. Se conserva porque S8-E2 y la preparación no ejecutada
# de EX-09 archivan su código; no es proveedor de ninguna medición futura.
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GEMINI_MODEL = env("GEMINI_MODEL", default="")
# Umbral de aplicación automática asistida (contrato S8-A, D5).
AI_CONFIDENCE_THRESHOLD = Decimal(env("AI_CONFIDENCE_THRESHOLD", default="0.75"))
# Reintentos **posteriores** al intento original: como máximo cuatro llamadas al
# proveedor por ejecución lógica. Los reintentos internos del SDK van deshabilitados
# para que esta sea la única capa que reintenta.
AI_MAX_RETRIES = env.int("AI_MAX_RETRIES", default=3)
AI_REQUEST_TIMEOUT_SECONDS = env.int("AI_REQUEST_TIMEOUT_SECONDS", default=20)

BREVO_API_KEY = env("BREVO_API_KEY", default="")

# Endpoint del envío. Se declara como ajuste —en lugar de incrustarlo en el código— para
# poder apuntarlo a un doble en pruebas sin parchear el módulo.
BREVO_API_URL = env("BREVO_API_URL", default="https://api.brevo.com/v3/smtp/email")

# Remitente. Admite la forma `Nombre <direccion>` de RFC 5322: el cliente la separa con
# `email.utils.parseaddr` y la envía a Brevo como `{"name": ..., "email": ...}`. La
# dirección debe ser un **remitente verificado** en Brevo, o el envío se rechaza.
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@ciudadania-activa.local")

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
    "apps.experiments_app",
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
    "apps.experiments_app.middleware.HttpInstrumentationMiddleware",
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
    # Tarifas con nombre para `ScopedRateThrottle`. **No** se declara
    # DEFAULT_THROTTLE_CLASSES: cada vista protegida activa explícitamente su scope.
    # En los scopes autenticados la clave del contador es el usuario.
    "DEFAULT_THROTTLE_RATES": {
        "identity_submit": "5/hour",
        "public_map": "120/min",
        "report_create": "5/hour",
        "possible_duplicates": "60/min",
        "reports_mine": "120/hour",
        "report_confirm": "30/hour",
        "reward_redeem": "10/hour",
        "public_ranking": "120/min",
    },
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

# El backend nativo de Django reutiliza el Redis ya aprovisionado. ``KEY_PREFIX``
# separa las claves ordinarias de la caché, pero no vuelve seguro ``cache.clear()``:
# la invalidación del mapa rota una generación y nunca vacía la base compartida.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "ciudadania_activa",
    }
}

PUBLIC_MAP_CACHE_TTL_SECONDS = env.int("PUBLIC_MAP_CACHE_TTL_SECONDS", default=60)
PUBLIC_RANKING_CACHE_TTL_SECONDS = env.int("PUBLIC_RANKING_CACHE_TTL_SECONDS", default=60)
PUBLIC_MAP_MAX_BBOX_SPAN_DEGREES = Decimal("0.5")
REPORT_DUPLICATE_RADIUS_METERS = env.int("REPORT_DUPLICATE_RADIUS_METERS", default=100)
REPORT_AUTO_CLOSE_HOURS = env.int("REPORT_AUTO_CLOSE_HOURS", default=72)
if REPORT_AUTO_CLOSE_HOURS <= 0:
    raise ImproperlyConfigured("REPORT_AUTO_CLOSE_HOURS debe ser un entero positivo.")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "reports-auto-close-every-15-minutes": {
        "task": "reports.auto_close",
        "schedule": crontab(minute="*/15"),
    },
}

# --- S11-B: instrumentación experimental ------------------------------------
# Todo queda apagado por defecto y exige, además, un entorno cuyo nombre, base,
# Redis y storage pasan el guard de aislamiento. Estos flags no se activan en
# producción ni se usan para datos existentes.
S11_EXPERIMENTS_ENABLED = env.bool("S11_EXPERIMENTS_ENABLED", default=False)
S11_ENVIRONMENT = env("S11_ENVIRONMENT", default="")
S11_DATABASE_URL = env("S11_DATABASE_URL", default="")
S11_ARTIFACT_ROOT = Path(env("S11_ARTIFACT_ROOT", default=str(REPO_ROOT / "var" / "s11-runs")))
S11_BACKUP_ROOT = Path(env("S11_BACKUP_ROOT", default=str(REPO_ROOT / "var" / "s11-backup")))
S11_RESTORE_ROOT = Path(env("S11_RESTORE_ROOT", default=str(REPO_ROOT / "var" / "s11-restore")))
S11_OBJECT_ROOT = Path(env("S11_OBJECT_ROOT", default=str(REPO_ROOT / "var" / "s11-objects")))
S11_ALLOW_SENSITIVE_PERSISTENCE = env.bool("S11_ALLOW_SENSITIVE_PERSISTENCE", default=False)
S11_CUSTODY_HMAC_KEY = env("S11_CUSTODY_HMAC_KEY", default="")
S11_PUBLICATION_APPROVAL_PUBLIC_KEY = env("S11_PUBLICATION_APPROVAL_PUBLIC_KEY", default="")
S11_LOCAL_STORAGE_ENABLED = env.bool("S11_LOCAL_STORAGE_ENABLED", default=False)
S11_FAKE_AI_ENABLED = env.bool("S11_FAKE_AI_ENABLED", default=False)

# Fijado en el Sprint 1: PostgreSQL/PostGIS reside en Supabase y se conecta siempre
# mediante DATABASE_URL. Ninguna rama de código distingue entornos; lo único que varía
# es el valor de DATABASE_URL (y, si hiciera falta, DB_SEARCH_PATH). Ver ADR-0003.
# El aislamiento entre la base local y Supabase se resuelve en infra/docker-compose.yml,
# que fija deliberadamente DATABASE_URL al contenedor local, no aquí.
