import uuid
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core.enums import ReportStatus
from apps.reports_app.models import Report, ReportMedia

pytestmark = pytest.mark.django_db

MINE_URL = "/api/v1/reports/mine/"


@pytest.fixture(autouse=True)
def isolated_throttles():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def verified_citizen(citizen):
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])
    return citizen


@pytest.fixture
def authenticated_api(verified_citizen):
    api = APIClient()
    api.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(verified_citizen).access_token}"
    )
    return api


def _report(citizen, category, index=0, **overrides):
    values = {
        "citizen": citizen,
        "category": category,
        "description": f"Reporte propio {index}.",
        "location": Point(-57.63 + index / 10000, -25.26, srid=4326),
    }
    values.update(overrides)
    return Report.objects.create(**values)


def test_mine_solo_devuelve_reportes_del_ciudadano_actual(
    authenticated_api,
    verified_citizen,
    other_citizen,
    category,
):
    own = _report(verified_citizen, category)
    _report(other_citizen, category, 1)

    response = authenticated_api.get(MINE_URL)

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert [item["id"] for item in response.json()["results"]] == [str(own.id)]


def test_mine_incluye_todos_los_estados(
    authenticated_api,
    verified_citizen,
    category,
):
    for index, status in enumerate(ReportStatus.values):
        _report(verified_citizen, category, index, status=status)

    response = authenticated_api.get(MINE_URL)

    assert response.status_code == 200
    assert {item["status"] for item in response.json()["results"]} == set(ReportStatus.values)


def test_mine_pagina_20_y_ordena_created_at_luego_id(
    authenticated_api,
    verified_citizen,
    category,
):
    base = timezone.now()
    reports = []
    for index in range(23):
        report = _report(verified_citizen, category, index)
        Report.objects.filter(pk=report.pk).update(created_at=base - timedelta(minutes=index))
        reports.append(report)

    first = authenticated_api.get(MINE_URL)
    second = authenticated_api.get(MINE_URL, {"page": 2})

    assert first.status_code == second.status_code == 200
    assert first.json()["count"] == 23
    assert len(first.json()["results"]) == 20
    assert len(second.json()["results"]) == 3
    assert first.json()["next"].endswith("/api/v1/reports/mine/?page=2")
    assert first.json()["previous"] is None
    assert second.json()["next"] is None
    assert second.json()["previous"].endswith("/api/v1/reports/mine/")
    assert [item["id"] for item in first.json()["results"]] == [
        str(report.id) for report in reports[:20]
    ]

    tied_time = base + timedelta(hours=1)
    low = _report(verified_citizen, category, 30, id=uuid.UUID(int=1))
    high = _report(verified_citizen, category, 31, id=uuid.UUID(int=2))
    Report.objects.filter(pk__in=[low.pk, high.pk]).update(created_at=tied_time)
    tied_response = authenticated_api.get(MINE_URL)
    assert [item["id"] for item in tied_response.json()["results"][:2]] == [
        str(high.id),
        str(low.id),
    ]


def test_mine_whitelist_no_expone_datos_privados(
    authenticated_api,
    verified_citizen,
    category,
):
    report = _report(verified_citizen, category, institution=category.default_institution)
    ReportMedia.objects.create(
        report=report,
        file_url="reports/privado.jpg",
        file_hash="1" * 64,
        uploaded_by=verified_citizen,
    )

    response = authenticated_api.get(MINE_URL)

    item = response.json()["results"][0]
    assert set(item) == {
        "id",
        "category",
        "institution",
        "description",
        "status",
        "priority",
        "created_at",
    }
    assert set(item["category"]) == {"id", "name", "slug"}
    assert set(item["institution"]) == {"id", "name", "slug"}
    serialized = str(response.json())
    assert "reports/privado.jpg" not in serialized
    assert "-57.63" not in serialized


def test_mine_anonimo_401():
    assert APIClient().get(MINE_URL).status_code == 401


def test_mine_ciudadano_no_verificado_403(citizen):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(citizen).access_token}")

    response = api.get(MINE_URL)

    assert response.status_code == 403


def test_mine_operador_autenticado_403(user_factory):
    operator = user_factory(email="operador-mine@example.com", role=UserRole.OPERATOR)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(operator).access_token}")

    response = api.get(MINE_URL)

    assert response.status_code == 403


def test_mine_rechaza_filtros_no_aprobados(authenticated_api):
    response = authenticated_api.get(MINE_URL, {"status": "CREADO"})

    assert response.status_code == 400
    assert "status" in response.json()["detail"]


def test_mine_throttle_independiente(authenticated_api, settings, monkeypatch):
    monkeypatch.setitem(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "reports_mine", "1/hour")

    first = authenticated_api.get(MINE_URL)
    second = authenticated_api.get(MINE_URL)

    assert first.status_code == 200
    assert second.status_code == 429


def test_mine_throttle_identifica_usuarios_por_separado(
    authenticated_api,
    other_citizen,
    settings,
    monkeypatch,
):
    monkeypatch.setitem(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "reports_mine", "1/hour")
    other_citizen.verification_status = VerificationStatus.APROBADO
    other_citizen.save(update_fields=["verification_status"])
    other_api = APIClient()
    other_api.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(other_citizen).access_token}"
    )

    assert authenticated_api.get(MINE_URL).status_code == 200
    assert authenticated_api.get(MINE_URL).status_code == 429
    assert other_api.get(MINE_URL).status_code == 200


def test_mine_veinte_resultados_sin_n_mas_uno(
    verified_citizen,
    category,
    django_assert_num_queries,
):
    for index in range(20):
        _report(
            verified_citizen,
            category,
            index,
            institution=category.default_institution,
        )
    api = APIClient()
    api.force_authenticate(verified_citizen)

    with django_assert_num_queries(2):
        response = api.get(MINE_URL)

    assert response.status_code == 200
    assert len(response.json()["results"]) == 20
