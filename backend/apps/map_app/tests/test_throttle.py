import pytest
from django.core.cache import cache

from apps.map_app.tests.conftest import PUBLIC_BBOX

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/public/reports/"
CATEGORIES_URL = "/api/v1/public/categories/"


def test_tarifa_publica_central_es_120_por_minuto(settings):
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["public_map"] == "120/min"
    assert "NUM_PROXIES" not in settings.REST_FRAMEWORK


def test_lista_y_detalle_comparten_scope_y_las_ip_se_separan(api_client, public_report_factory):
    report = public_report_factory()
    first_ip = "198.51.100.10"
    second_ip = "198.51.100.11"
    cache.delete(f"throttle_public_map_{first_ip}")
    cache.delete(f"throttle_public_map_{second_ip}")

    first = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX}, REMOTE_ADDR=first_ip)
    second = api_client.get(f"{LIST_URL}{report.pk}/", REMOTE_ADDR=first_ip)
    categories = api_client.get(CATEGORIES_URL, REMOTE_ADDR=first_ip)
    for _ in range(117):
        assert (
            api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX}, REMOTE_ADDR=first_ip).status_code == 200
        )
    blocked = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX}, REMOTE_ADDR=first_ip)
    other_ip = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX}, REMOTE_ADDR=second_ip)

    assert (
        first.status_code
        == second.status_code
        == categories.status_code
        == other_ip.status_code
        == 200
    )
    assert blocked.status_code == 429
