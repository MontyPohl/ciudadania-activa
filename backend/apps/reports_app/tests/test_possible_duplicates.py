import uuid

import pytest
from django.contrib.gis.geos import GEOSGeometry, Point
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import Category
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core.enums import ReportStatus
from apps.reports_app.models import Report
from apps.reports_app.selectors import ACTIVE_REPORT_STATUSES, find_possible_duplicates
from apps.reports_app.serializers import ReportSummarySerializer

pytestmark = pytest.mark.django_db

DUPLICATES_URL = "/api/v1/reports/possible-duplicates/"
CENTER = Point(-57.6359, -25.2637, srid=4326)


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
    token = RefreshToken.for_user(verified_citizen).access_token
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


def _project(distance_meters: float, center: Point = CENTER) -> Point:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ST_AsEWKB(
                ST_Project(
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                    %s,
                    radians(90)
                )::geometry
            )
            """,
            [center.x, center.y, distance_meters],
        )
        (ewkb,) = cursor.fetchone()
    return GEOSGeometry(memoryview(ewkb))


def _report(category, citizen, distance, *, center=CENTER, **overrides):
    values = {
        "citizen": citizen,
        "category": category,
        "description": f"Reporte a {distance} metros.",
        "location": _project(distance, center),
    }
    values.update(overrides)
    return Report.objects.create(**values)


def _params(category, **overrides):
    values = {"lat": str(CENTER.y), "lng": str(CENTER.x), "category_id": str(category.id)}
    values.update(overrides)
    return values


def test_estados_activos_neutrales_son_los_cuatro_aprobados():
    assert ACTIVE_REPORT_STATUSES == (
        ReportStatus.CREADO,
        ReportStatus.CLASIFICADO,
        ReportStatus.ASIGNADO,
        ReportStatus.EN_PROCESO,
    )


@pytest.mark.parametrize("status", ACTIVE_REPORT_STATUSES)
def test_selector_incluye_cada_estado_activo(status, category, citizen):
    report = _report(category, citizen, 50, status=status)

    found = list(
        find_possible_duplicates(
            category=category,
            latitude=CENTER.y,
            longitude=CENTER.x,
        )
    )

    assert found == [report]


@pytest.mark.parametrize(
    "status",
    [ReportStatus.RECHAZADO, ReportStatus.RESUELTO, ReportStatus.CERRADO],
)
def test_selector_excluye_estados_no_activos(status, category, citizen):
    _report(category, citizen, 50, status=status)

    assert not list(
        find_possible_duplicates(
            category=category,
            latitude=CENTER.y,
            longitude=CENTER.x,
        )
    )


def test_selector_incluye_boundary_100_y_excluye_fuera(category, citizen):
    inside = _report(category, citizen, 99.99)
    boundary = _report(category, citizen, 99.999)
    outside = _report(category, citizen, 100.01)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, ST_Distance(location::geography, %s::geography)
            FROM reports
            WHERE id IN (%s, %s, %s)
            """,
            [CENTER.ewkt, inside.id, boundary.id, outside.id],
        )
        measured = {report_id: distance for report_id, distance in cursor.fetchall()}

    assert round(measured[inside.id], 2) == 99.99
    assert round(measured[boundary.id], 2) == 100.00
    assert round(measured[outside.id], 2) == 100.01

    found = list(
        find_possible_duplicates(
            category=category,
            latitude=CENTER.y,
            longitude=CENTER.x,
        )
    )

    assert found == [inside, boundary]


@pytest.mark.parametrize("latitude", [-25.1, -25.3, -25.5])
def test_prefiltro_gist_no_excluye_candidatos_validos_en_asuncion(
    latitude,
    category,
    citizen,
):
    center = Point(CENTER.x, latitude, srid=4326)
    candidate = _report(category, citizen, 99.99, center=center)

    found = list(
        find_possible_duplicates(
            category=category,
            latitude=center.y,
            longitude=center.x,
        )
    )

    assert found == [candidate]


def test_selector_excluye_categoria_distinta(category, citizen):
    other_category = Category.objects.exclude(pk=category.pk).filter(is_active=True).first()
    assert other_category is not None
    _report(other_category, citizen, 20)

    assert not list(
        find_possible_duplicates(
            category=category,
            latitude=CENTER.y,
            longitude=CENTER.x,
        )
    )


def test_selector_limita_cinco_y_ordena_distancia_luego_id(category, citizen):
    distances = [80, 20, 60, 10, 40, 30]
    reports = [_report(category, citizen, distance) for distance in distances]

    found = list(
        find_possible_duplicates(
            category=category,
            latitude=CENTER.y,
            longitude=CENTER.x,
        )
    )

    assert [report.id for report in found] == [reports[index].id for index in [3, 1, 5, 4, 2]]

    lower_id = uuid.UUID(int=1)
    higher_id = uuid.UUID(int=2)
    Report.objects.all().delete()
    high = _report(category, citizen, 25, id=higher_id)
    low = _report(category, citizen, 25, id=lower_id)
    tied = list(
        find_possible_duplicates(
            category=category,
            latitude=CENTER.y,
            longitude=CENTER.x,
        )
    )
    assert tied == [low, high]


def test_selector_conserva_prefiltro_gist_y_recheck_geography(category):
    queryset = find_possible_duplicates(
        category=category,
        latitude=CENTER.y,
        longitude=CENTER.x,
    )
    sql = str(queryset.query)

    assert "&&" in sql
    assert "ST_Buffer" in sql
    assert "ST_DWithin" in sql
    assert "::geography" in sql


def test_api_devuelve_lista_directa_whitelist_y_no_cachea(
    authenticated_api,
    category,
    citizen,
):
    report = _report(category, citizen, 20, institution=None)

    response = authenticated_api.get(DUPLICATES_URL, _params(category))

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert isinstance(response.json(), list)
    assert response.json() == [
        {
            "id": str(report.id),
            "category": {
                "id": str(category.id),
                "name": category.name,
                "slug": category.slug,
            },
            "institution": None,
            "description": report.description,
            "status": report.status,
            "priority": report.priority,
            "created_at": timezone.localtime(report.created_at).isoformat(),
        }
    ]
    serialized = response.json()[0]
    forbidden = {
        "distance",
        "latitude",
        "longitude",
        "location",
        "citizen",
        "email",
        "source",
        "media",
        "file_hash",
        "file_url",
        "reward",
    }
    assert forbidden.isdisjoint(serialized)


def test_api_sin_candidatos_devuelve_lista_vacia(authenticated_api, category):
    response = authenticated_api.get(DUPLICATES_URL, _params(category))

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"lat": "NaN"},
        {"lat": "91"},
        {"lng": "181"},
        {"category_id": "no-es-uuid"},
        {"extra": "no"},
    ],
)
def test_api_rechaza_parametros_invalidos(authenticated_api, category, overrides):
    response = authenticated_api.get(DUPLICATES_URL, _params(category, **overrides))

    assert response.status_code == 400


def test_api_rechaza_categoria_inactiva(authenticated_api, category):
    category.is_active = False
    category.save(update_fields=["is_active"])

    response = authenticated_api.get(DUPLICATES_URL, _params(category))

    assert response.status_code == 400
    assert "category_id" in response.json()["detail"]


def test_api_rechaza_categoria_uuid_inexistente(authenticated_api, category):
    response = authenticated_api.get(
        DUPLICATES_URL,
        _params(category, category_id=str(uuid.uuid4())),
    )

    assert response.status_code == 400
    assert "category_id" in response.json()["detail"]


def test_api_anonima_401(category):
    response = APIClient().get(DUPLICATES_URL, _params(category))

    assert response.status_code == 401


def test_api_ciudadano_no_verificado_403(citizen, category):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(citizen).access_token}")

    response = api.get(DUPLICATES_URL, _params(category))

    assert response.status_code == 403


def test_api_operador_autenticado_403(user_factory, category):
    operator = user_factory(email="operador-duplicados@example.com", role=UserRole.OPERATOR)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(operator).access_token}")

    response = api.get(DUPLICATES_URL, _params(category))

    assert response.status_code == 403


def test_api_throttle_possible_duplicates(authenticated_api, category, settings, monkeypatch):
    monkeypatch.setitem(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "possible_duplicates", "1/min"
    )

    first = authenticated_api.get(DUPLICATES_URL, _params(category))
    second = authenticated_api.get(DUPLICATES_URL, _params(category))

    assert first.status_code == 200
    assert second.status_code == 429


def test_api_no_registra_coordenadas_en_logs(authenticated_api, category, caplog):
    with caplog.at_level("DEBUG"):
        response = authenticated_api.get(DUPLICATES_URL, _params(category))

    assert response.status_code == 200
    assert str(CENTER.x) not in caplog.text
    assert str(CENTER.y) not in caplog.text


def test_selector_y_serializer_no_generan_n_mas_uno(
    category,
    citizen,
    django_assert_num_queries,
):
    for distance in [10, 20, 30, 40, 50]:
        _report(
            category,
            citizen,
            distance,
            institution=category.default_institution,
        )

    queryset = find_possible_duplicates(
        category=category,
        latitude=CENTER.y,
        longitude=CENTER.x,
    )
    with django_assert_num_queries(1):
        payload = ReportSummarySerializer(queryset, many=True).data

    assert len(payload) == 5
