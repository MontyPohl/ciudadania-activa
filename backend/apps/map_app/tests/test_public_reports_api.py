import uuid
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.admin_app.models import Category
from apps.core.enums import ReportStatus
from apps.map_app.tests.conftest import PUBLIC_BBOX

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/public/reports/"
CATEGORIES_URL = "/api/v1/public/categories/"


def _list(api_client, **params):
    return api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX, **params})


def test_bbox_es_obligatorio_y_el_error_usa_el_handler_uniforme(api_client):
    response = api_client.get(LIST_URL)

    assert response.status_code == 400
    assert set(response.json()) == {"detail"}
    assert "bbox" in response.json()["detail"]


def test_default_active_y_los_tres_filtros(api_client, public_report_factory):
    for status in ReportStatus:
        public_report_factory(status=status)

    default = _list(api_client).json()
    active = _list(api_client, state_filter="active").json()
    resolved = _list(api_client, state_filter="resolved").json()
    all_public = _list(api_client, state_filter="all").json()

    assert default["count"] == active["count"] == 4
    assert resolved["count"] == 2
    assert all_public["count"] == 6
    assert {row["status"] for row in default["results"]} == {
        ReportStatus.CREADO,
        ReportStatus.CLASIFICADO,
        ReportStatus.ASIGNADO,
        ReportStatus.EN_PROCESO,
    }


def test_lista_positiva_excluye_rechazado_y_un_estado_futuro(api_client, public_report_factory):
    rejected = public_report_factory(status=ReportStatus.RECHAZADO)
    future = public_report_factory(status="FUTURO")

    response = _list(api_client, state_filter="all")

    ids = {row["id"] for row in response.json()["results"]}
    assert str(rejected.pk) not in ids
    assert str(future.pk) not in ids


def test_rechazado_y_uuid_inexistente_tienen_el_mismo_404(api_client, public_report_factory):
    rejected = public_report_factory(status=ReportStatus.RECHAZADO)

    rejected_response = api_client.get(f"{LIST_URL}{rejected.pk}/")
    missing_response = api_client.get(f"{LIST_URL}{uuid.uuid4()}/")

    assert rejected_response.status_code == missing_response.status_code == 404
    assert rejected_response.json() == missing_response.json()


def test_filtro_categoria_es_slug_y_combina_and_con_estado_y_bbox(
    api_client, public_report_factory
):
    other_category = Category.objects.get(slug="alumbrado-publico")
    wanted = public_report_factory(status=ReportStatus.RESUELTO, category=other_category)
    public_report_factory(status=ReportStatus.CREADO, category=other_category)
    public_report_factory(status=ReportStatus.RESUELTO)
    public_report_factory(
        status=ReportStatus.RESUELTO,
        category=other_category,
        location=Point(-58.5, -25.2, srid=4326),
    )

    response = _list(api_client, state_filter="resolved", category=other_category.slug)

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [str(wanted.pk)]


def test_all_prioriza_activos_y_desempata_establemente(api_client, public_report_factory):
    active_low = public_report_factory(status=ReportStatus.CREADO)
    active_high = public_report_factory(status=ReportStatus.CREADO)
    resolved = public_report_factory(status=ReportStatus.RESUELTO)
    same_time = timezone.now() - timedelta(days=1)
    type(active_low).objects.filter(pk__in=[active_low.pk, active_high.pk, resolved.pk]).update(
        created_at=same_time
    )

    results = _list(api_client, state_filter="all").json()["results"]

    assert [row["status"] for row in results] == [
        ReportStatus.CREADO,
        ReportStatus.CREADO,
        ReportStatus.RESUELTO,
    ]
    assert [row["id"] for row in results[:2]] == sorted(
        [str(active_low.pk), str(active_high.pk)], reverse=True
    )


def test_paginacion_fija_20_ignora_page_size_y_emite_enlace_canonico(
    api_client, public_report_factory
):
    for _ in range(21):
        public_report_factory()

    response = api_client.get(
        LIST_URL,
        {
            "bbox": "-57.7999,-25.4999,-57.3001,-25.0001",
            "page_size": 100,
        },
    )
    payload = response.json()

    assert len(payload["results"]) == 20
    assert payload["count"] == 21
    assert "page_size" not in payload["next"]
    assert "bbox=-57.8,-25.5,-57.3,-25" in payload["next"]
    assert "state_filter=active" in payload["next"]

    second_page = api_client.get(payload["next"])
    assert second_page.status_code == 200
    assert second_page.json()["count"] == 21
    assert len(second_page.json()["results"]) == 1
    assert second_page.json()["next"] is None
    assert second_page.json()["previous"] is not None
    assert {row["id"] for row in payload["results"]}.isdisjoint(
        {row["id"] for row in second_page.json()["results"]}
    )


def test_catalogo_publico_completo_minimo_estable_y_sin_paginacion(api_client):
    response = api_client.get(CATEGORIES_URL)
    payload = response.json()
    repeated = api_client.get(CATEGORIES_URL).json()

    assert response.status_code == 200
    assert isinstance(payload, list)
    assert len(payload) == 10
    assert all(set(item) == {"id", "name", "slug"} for item in payload)
    assert [item["id"] for item in payload] == [item["id"] for item in repeated]
    assert [item["id"] for item in payload] == [
        str(category_id)
        for category_id in Category.objects.filter(is_active=True)
        .order_by("name", "id")
        .values_list("id", flat=True)
    ]


def test_catalogo_publico_excluye_categoria_inactiva(api_client, category):
    category.is_active = False
    category.save(update_fields=["is_active", "updated_at"])

    payload = api_client.get(CATEGORIES_URL).json()

    assert category.slug not in {item["slug"] for item in payload}


@pytest.mark.parametrize("url", [LIST_URL, CATEGORIES_URL])
def test_endpoints_publicos_ignoran_jwt_invalido(api_client, url):
    api_client.credentials(HTTP_AUTHORIZATION="Bearer token-invalido")
    params = {"bbox": PUBLIC_BBOX} if url == LIST_URL else None

    assert api_client.get(url, params).status_code == 200


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("url", [LIST_URL, CATEGORIES_URL])
def test_colecciones_no_aceptan_escritura(api_client, method, url):
    assert getattr(api_client, method)(url, {}).status_code == 405


def test_detalle_publico_no_acepta_escritura(api_client, public_report_factory):
    report = public_report_factory()

    assert api_client.patch(f"{LIST_URL}{report.pk}/", {}).status_code == 405


def test_jwt_invalido_no_oculta_el_405_de_escritura(api_client):
    api_client.credentials(HTTP_AUTHORIZATION="Bearer token-invalido")

    assert api_client.post(LIST_URL, {}).status_code == 405
