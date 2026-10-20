"""Permisos, entrada estricta y transporte del API de métricas."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.utils import timezone
from redis.exceptions import RedisError
from rest_framework.test import APIClient

from apps.admin_app.models import Institution, InstitutionOperator
from apps.analytics_app.periods import current_period, format_period
from apps.analytics_app.selectors import LOCAL_TZ

BASE = "/api/v1/"


@pytest.mark.parametrize(
    "role,path,query,expected",
    [
        (None, "operator", "", 401),
        (None, "admin", "", 401),
        ("citizen", "operator", "", 403),
        ("citizen", "admin", "", 403),
        ("admin_user", "operator", "", 403),
        ("operator_user", "admin", "", 403),
        ("operator_user", "operator", "foreign", 403),
        ("operator_user", "operator", "multiple", 400),
        ("operator_user", "operator", "", 200),
        ("admin_user", "admin", "", 200),
        ("admin_user", "admin", "own", 200),
        ("admin_user", "admin", "missing", 404),
        (None, "public", "", 200),
        (None, "public", "bad-token", 200),
    ],
)
@pytest.mark.django_db
def test_permisos_y_alcance(request, institution, role, path, query, expected):
    client = APIClient()
    user = request.getfixturevalue(role) if role else None
    if user:
        client.force_authenticate(user)
    suffix = ""
    if query == "foreign":
        other = Institution.objects.create(name="Ajena de métricas", slug="metrics-foreign")
        suffix = f"?institution_id={other.pk}"
    elif query == "multiple":
        other = Institution.objects.create(name="Segundo vínculo", slug="metrics-second")
        InstitutionOperator.objects.create(user=user, institution=other)
    elif query == "missing":
        suffix = f"?institution_id={uuid4()}"
    elif query == "own":
        suffix = f"?institution_id={institution.pk}"
    elif query == "bad-token":
        client.credentials(HTTP_AUTHORIZATION="Bearer basura")
    # Solo las claves de esta prueba; nunca se borra toda la caché.
    month = format_period(current_period())
    for key in (
        f"metrics:v1:inst:{institution.pk}:{month}",
        f"metrics:v1:all:{month}",
        f"public-metrics:v1:{timezone.now().astimezone(LOCAL_TZ).date().isoformat()}",
    ):
        cache.delete(key)
    response = client.get(f"{BASE}{path}/metrics/{suffix}")
    assert response.status_code == expected
    if expected == 200 and path == "operator":
        assert response.data["institution"]["id"] == str(institution.pk)
    if expected == 200 and path == "admin":
        assert response.data["scope"] == ("institution" if query == "own" else "all")
        assert (response.data["by_institution"] is None) == (query == "own")


@pytest.mark.parametrize(
    "path,query",
    [
        (path, query)
        for path in ("operator", "admin")
        for query in (
            "unknown=1",
            "period=2026-01&period=2026-02",
            "period=2026-13",
            "period=9999-12",
            "period=2025-12",
            "period=%202026-01",
        )
    ]
    + [("public", "period=2026-01"), ("public", "unknown=1")],
)
@pytest.mark.django_db
def test_parametros_estrictos(request, path, query):
    client = APIClient()
    if path != "public":
        client.force_authenticate(
            request.getfixturevalue("operator_user" if path == "operator" else "admin_user")
        )
    response = client.get(f"{BASE}{path}/metrics/?{query}")
    assert response.status_code == 400
    assert set(response.data) == {"detail"}
    assert isinstance(response.data["detail"], dict)


@pytest.mark.django_db
def test_cabeceras_y_cache_con_fallback(operator_user, admin_user, institution, report):
    client = APIClient()
    generated = {}
    with patch("apps.analytics_app.cache.cache") as metrics_cache:
        metrics_cache.get.side_effect = RedisError("Sin Redis")
        for path, user, header in [
            ("operator", operator_user, "private, no-store"),
            ("admin", admin_user, "private, no-store"),
            ("public", None, "public, max-age=60"),
        ]:
            client.force_authenticate(user)
            response = client.get(f"{BASE}{path}/metrics/")
            assert response.status_code == 200
            assert response["Cache-Control"] == header
            assert response.data["generated_at"]
            generated[path] = response.data
        assert metrics_cache.get.call_count == 3
        metrics_cache.set.assert_not_called()
    assert generated["operator"]["reports"]["received"] == 1
    assert generated["admin"]["reports"]["received"] == 1
    assert generated["public"]["totals"]["received"] == 1
    forbidden = {
        "email",
        "user",
        "user_id",
        "citizen",
        "full_name",
        "description",
        "location",
        "latitude",
        "longitude",
    }

    def inspect(value, parent=None):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            if "id" in value:
                assert parent == "institution"
            for key, child in value.items():
                inspect(child, key)
        elif isinstance(value, list):
            for child in value:
                inspect(child, parent)

    inspect(generated["public"])
    # Un acierto de caché conserva generated_at y evita recalcular los agregados.
    key = f"metrics:v1:inst:{institution.pk}:{format_period(current_period())}"
    cache.delete(key)
    client.force_authenticate(operator_user)
    first = client.get(f"{BASE}operator/metrics/")
    with patch(
        "apps.analytics_app.views_operator.selectors.institution_metrics",
        side_effect=AssertionError("No debe recalcular"),
    ):
        second = client.get(f"{BASE}operator/metrics/")
    assert first.data == second.data
    cache.delete(key)
