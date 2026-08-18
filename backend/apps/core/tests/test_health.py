import importlib

import pytest
from django.test import Client, override_settings
from rest_framework.test import APIClient


@pytest.fixture
def prod_security_settings(monkeypatch):
    """Valores de seguridad reales de `config.settings.prod`.

    Se leen del módulo en lugar de duplicarlos para que la prueba no derive si
    `prod.py` cambia. Las guardas de `prod.py` exigen `DJANGO_ALLOWED_HOSTS` y
    `DJANGO_SECRET_KEY`, que aquí se satisfacen con valores de prueba.
    """
    import config.settings.base as base

    monkeypatch.setattr(base, "ALLOWED_HOSTS", ["healthcheck.railway.app"])
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-secret-key")
    prod = importlib.reload(importlib.import_module("config.settings.prod"))

    return {
        "SECURE_SSL_REDIRECT": prod.SECURE_SSL_REDIRECT,
        # `getattr` con defecto para que, si el ajuste desapareciera, la prueba falle
        # reproduciendo el 301 real de Railway en vez de con un AttributeError.
        "SECURE_REDIRECT_EXEMPT": getattr(prod, "SECURE_REDIRECT_EXEMPT", []),
        "SECURE_PROXY_SSL_HEADER": prod.SECURE_PROXY_SSL_HEADER,
        "ALLOWED_HOSTS": [
            "healthcheck.railway.app",
            "ciudadania-activa-production.up.railway.app",
        ],
    }


@pytest.fixture
def healthcheck_dependencies_up(monkeypatch):
    """Aísla la prueba de PostgreSQL y Redis: mide solo middleware y vista."""
    monkeypatch.setattr("apps.core.views._database_is_up", lambda: True)
    monkeypatch.setattr("apps.core.views._redis_is_up", lambda: True)


@pytest.mark.django_db
def test_health_endpoint_returns_ok_status():
    response = APIClient().get("/api/v1/health/")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["redis"] is True


def test_railway_healthcheck_over_plain_http_returns_200(
    prod_security_settings, healthcheck_dependencies_up
):
    """Reproduce el healthcheck de Railway: HTTP plano, sin X-Forwarded-Proto.

    Antes de exentar la ruta en `SECURE_REDIRECT_EXEMPT`, SecurityMiddleware
    respondía 301 hacia HTTPS y el deploy fallaba con «Healthcheck failure».
    """
    with override_settings(**prod_security_settings):
        response = Client().get("/api/v1/health/", headers={"host": "healthcheck.railway.app"})

    assert response.status_code != 301, "el healthcheck vuelve a redirigir a HTTPS"
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_non_healthcheck_routes_still_redirect_to_https(prod_security_settings):
    """SECURE_SSL_REDIRECT sigue vigente fuera del healthcheck."""
    with override_settings(**prod_security_settings):
        response = Client().get(
            "/admin/", headers={"host": "ciudadania-activa-production.up.railway.app"}
        )

    assert response.status_code == 301
    assert response["Location"].startswith("https://")
