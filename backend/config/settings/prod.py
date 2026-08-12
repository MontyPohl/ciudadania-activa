from .base import *  # noqa: F403

DEBUG = False

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("DJANGO_ALLOWED_HOSTS es obligatorio en producción.")

if not env("DJANGO_SECRET_KEY", default=None):  # noqa: F405
    raise RuntimeError("DJANGO_SECRET_KEY es obligatorio en producción.")
