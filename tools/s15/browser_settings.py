"""DEBUG=False/CORS exacto para navegador local; no altera el perfil de medición."""

import os

from tools.s15.browser_profile import validate_browser_environment

validate_browser_environment(os.environ)

from config.settings.base import *  # noqa: E402,F403

validate_browser_environment(os.environ)

DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = ["http://127.0.0.1:3115"]
AI_CLASSIFICATION_ENABLED = False
AI_PROVIDER = ""
OPENAI_API_KEY = ""
GEMINI_API_KEY = ""
GOOGLE_OAUTH_CLIENT_ID = ""
S11_EXPERIMENTS_ENABLED = False
S11_LOCAL_STORAGE_ENABLED = False
S11_FAKE_AI_ENABLED = False
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PUBLIC_MAP_CACHE_TTL_SECONDS = 60
PUBLIC_RANKING_CACHE_TTL_SECONDS = 60
DASHBOARD_METRICS_CACHE_TTL_SECONDS = 120
PUBLIC_METRICS_CACHE_TTL_SECONDS = 300
CELERY_BEAT_SCHEDULE = {}
