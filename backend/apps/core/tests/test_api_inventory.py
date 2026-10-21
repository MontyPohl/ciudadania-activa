"""Inventario de la API pública y privada contra docs/API.md (S14-D07).

`ENDPOINTS` es el índice de API §11: cada ruta real bajo `api/`, sus métodos y lo
que recibe un anónimo. Si se agrega, renombra o borra una ruta sin actualizar el
índice y la documentación, la primera prueba falla.
"""

import re
from uuid import uuid4

import pytest
from django.urls import URLResolver, get_resolver
from rest_framework.test import APIClient

EXAMPLE_ID = str(uuid4())
INVALID_TOKEN = "token-invalido-s14"

# Ruta con IDs de ejemplo, métodos, método anónimo, código y sección de API.md.
ENDPOINTS = [
    ("/api/v1/health/", {"GET"}, "get", 200, "1.1"),
    # §2.1 y los serializadores de ambas vistas: sin cuerpo corresponde 400.
    ("/api/v1/auth/google/", {"POST"}, "post", 400, "2.1"),
    ("/api/v1/auth/refresh/", {"POST"}, "post", 400, "2.1"),
    ("/api/v1/auth/me/", {"GET"}, "get", 401, "2.1"),
    ("/api/v1/identity/submit/", {"POST"}, "post", 401, "3.1"),
    ("/api/v1/identity/status/", {"GET"}, "get", 401, "3.2"),
    ("/api/v1/profile/ranking-visibility/", {"GET", "PATCH"}, "get", 401, "3.3"),
    ("/api/v1/admin/identity/", {"GET"}, "get", 401, "3.4"),
    (f"/api/v1/admin/identity/{EXAMPLE_ID}/", {"GET"}, "get", 401, "3.5"),
    (f"/api/v1/admin/identity/{EXAMPLE_ID}/review/", {"PATCH"}, "patch", 401, "3.6"),
    # §4.1 y PublicReportQuerySerializer: bbox es obligatorio.
    ("/api/v1/public/reports/", {"GET"}, "get", 400, "4.1"),
    (f"/api/v1/public/reports/{EXAMPLE_ID}/", {"GET"}, "get", 404, "4.2"),
    (f"/api/v1/public/reports/{EXAMPLE_ID}/photo/", {"GET"}, "get", 404, "S15-B1"),
    (
        f"/api/v1/public/reports/{EXAMPLE_ID}/evidence/{EXAMPLE_ID}/",
        {"GET"},
        "get",
        404,
        "S15-B1",
    ),
    ("/api/v1/public/categories/", {"GET"}, "get", 200, "4.3"),
    ("/api/v1/reports/", {"POST"}, "post", 401, "5.1"),
    ("/api/v1/reports/possible-duplicates/", {"GET"}, "get", 401, "5.2"),
    ("/api/v1/reports/mine/", {"GET"}, "get", 401, "6.1"),
    (f"/api/v1/reports/{EXAMPLE_ID}/", {"GET"}, "get", 401, "6.2"),
    (f"/api/v1/reports/{EXAMPLE_ID}/history/", {"GET"}, "get", 401, "6.3"),
    (f"/api/v1/reports/{EXAMPLE_ID}/confirm/", {"POST"}, "post", 401, "6.4"),
    ("/api/v1/citizen/summary/", {"GET"}, "get", 401, "6.5"),
    (f"/api/v1/reports/{EXAMPLE_ID}/evidence/", {"GET"}, "get", 401, "7.6"),
    # §7.6 y ResolutionEvidenceDownloadView: la firma inválida produce 404.
    (f"/api/v1/reports/evidence-access/{INVALID_TOKEN}/", {"GET"}, "get", 404, "7.6"),
    ("/api/v1/operator/reports/", {"GET"}, "get", 401, "7.2"),
    (f"/api/v1/operator/reports/{EXAMPLE_ID}/", {"GET"}, "get", 401, "7.3"),
    (f"/api/v1/operator/reports/{EXAMPLE_ID}/history/", {"GET"}, "get", 401, "7.4"),
    (f"/api/v1/operator/reports/{EXAMPLE_ID}/validate/", {"POST"}, "post", 401, "7.5"),
    (f"/api/v1/operator/reports/{EXAMPLE_ID}/reject/", {"POST"}, "post", 401, "7.5"),
    (f"/api/v1/operator/reports/{EXAMPLE_ID}/status/", {"PATCH"}, "patch", 401, "7.6"),
    (f"/api/v1/operator/reports/{EXAMPLE_ID}/evidence/", {"GET", "POST"}, "get", 401, "7.6"),
    ("/api/v1/admin/audit-logs/", {"GET"}, "get", 401, "7.6, 8.7"),
    (f"/api/v1/admin/reports/{EXAMPLE_ID}/evidence/", {"GET"}, "get", 401, "7.6"),
    (f"/api/v1/admin/reports/{EXAMPLE_ID}/revert-validation/", {"POST"}, "post", 401, "7.6"),
    ("/api/v1/rewards/summary/", {"GET"}, "get", 401, "8.3"),
    ("/api/v1/rewards/transactions/", {"GET"}, "get", 401, "8.3"),
    ("/api/v1/rewards/catalog/", {"GET"}, "get", 401, "8.3"),
    ("/api/v1/rewards/redemptions/", {"GET"}, "get", 401, "8.3"),
    (f"/api/v1/rewards/redemptions/{EXAMPLE_ID}/", {"GET"}, "get", 401, "8.3"),
    (f"/api/v1/rewards/{EXAMPLE_ID}/redeem/", {"POST"}, "post", 401, "8.3"),
    ("/api/v1/operator/rewards/", {"GET", "POST"}, "get", 401, "8.4"),
    (f"/api/v1/operator/rewards/{EXAMPLE_ID}/", {"PATCH"}, "patch", 401, "8.4"),
    ("/api/v1/operator/rewards/redemptions/", {"GET"}, "get", 401, "8.4"),
    ("/api/v1/ranking/monthly/", {"GET"}, "get", 200, "8.5"),
    ("/api/v1/operator/metrics/", {"GET"}, "get", 401, "10"),
    ("/api/v1/admin/metrics/", {"GET"}, "get", 401, "10"),
    ("/api/v1/public/metrics/", {"GET"}, "get", 200, "10"),
]

DOCUMENTED_ABSENT = [
    ("/api/v1/auth/logout/", "post", 404, "2.1"),
    ("/api/v1/x-mock/messages/", "post", 404, "10.3"),
    ("/api/v1/telegram/webhook/", "post", 404, "CT-43"),
]

# Solo los endpoints cuyas pruebas de app ya fijan este contrato de autenticación.
STABLE_AUTHENTICATION_ROUTES = {
    f"/api/v1/reports/{EXAMPLE_ID}/evidence/",
    f"/api/v1/operator/reports/{EXAMPLE_ID}/evidence/",
    f"/api/v1/operator/reports/{EXAMPLE_ID}/status/",
    "/api/v1/admin/audit-logs/",
    f"/api/v1/admin/reports/{EXAMPLE_ID}/evidence/",
    f"/api/v1/admin/reports/{EXAMPLE_ID}/revert-validation/",
}


def _normalize_route(route):
    route = route.replace(EXAMPLE_ID, "{id}").replace(INVALID_TOKEN, "{token}")
    route = re.sub(r"<uuid:[^>]+>", "{id}", route)
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", route)


def _api_routes(patterns, prefix=""):
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _api_routes(pattern.url_patterns, route)
        elif route.startswith("api/"):
            callback = pattern.callback
            view_class = getattr(callback, "view_class", None) or callback.cls
            methods = frozenset(
                method.upper()
                for method in view_class.http_method_names
                if method not in {"options", "head"} and hasattr(view_class, method)
            )
            yield _normalize_route(f"/{route}"), methods


def test_las_rutas_coinciden_con_el_indice_documentado():
    expected = {
        (_normalize_route(route), frozenset(methods)) for route, methods, _, _, _ in ENDPOINTS
    }

    assert set(_api_routes(get_resolver().url_patterns)) == expected


@pytest.mark.parametrize(
    "route, method, expected_status, section",
    [
        pytest.param(route, method, expected, section, id=f"{method.upper()} {route}")
        for route, _, method, expected, section in ENDPOINTS
    ]
    + [
        pytest.param(route, method, expected, section, id=f"{method.upper()} {route}")
        for route, method, expected, section in DOCUMENTED_ABSENT
    ],
)
@pytest.mark.django_db
def test_cada_endpoint_responde_al_anonimo_como_documenta(
    route, method, expected_status, section, settings, monkeypatch
):
    # Cada caso usa memoria propia, sin Redis compartido ni contadores heredados.
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"s14-api-inventory:{method}:{route}",
        }
    }
    if route == "/api/v1/health/":
        # El inventario verifica HTTP, sin abrir una conexión para el ping de Redis.
        monkeypatch.setattr("apps.core.views._redis_is_up", lambda: True)

    response = getattr(APIClient(), method)(route)

    assert response.status_code == expected_status, f"{method.upper()} {route}: API §{section}"
    if expected_status == 401 and route in STABLE_AUTHENTICATION_ROUTES:
        assert response.json() == {
            "detail": "Se requiere autenticación.",
            "code": "AUTHENTICATION_REQUIRED",
        }
