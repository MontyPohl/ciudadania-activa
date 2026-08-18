from .base import *  # noqa: F403

DEBUG = False

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Railway consulta el healthcheck contra el contenedor por HTTP plano y sin
# X-Forwarded-Proto, de modo que SecurityMiddleware respondía 301 hacia HTTPS y el
# deploy fallaba con «Healthcheck failure». Solo esta ruta queda exenta del redirect;
# el resto del tráfico sigue forzado a HTTPS por SECURE_SSL_REDIRECT.
SECURE_REDIRECT_EXEMPT = [r"^api/v1/health/$"]

if not ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("DJANGO_ALLOWED_HOSTS es obligatorio en producción.")

if not env("DJANGO_SECRET_KEY", default=None):  # noqa: F405
    raise RuntimeError("DJANGO_SECRET_KEY es obligatorio en producción.")
