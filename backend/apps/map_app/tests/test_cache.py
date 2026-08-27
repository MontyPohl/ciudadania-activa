import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.cache import cache
from redis.exceptions import RedisError
from rest_framework.test import APIRequestFactory

from apps.core.enums import ReportStatus
from apps.map_app.bbox import parse_and_canonicalize_bbox
from apps.map_app.cache import (
    GENERATION_KEY,
    build_public_list_cache_key,
    get_public_list,
    invalidate_public_reports_cache,
    set_public_list,
)
from apps.map_app.tests.conftest import PUBLIC_BBOX

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/public/reports/"


def _params(raw_bbox=PUBLIC_BBOX, *, page=1, state_filter="active", category=None):
    return {
        "bbox": parse_and_canonicalize_bbox(raw_bbox),
        "page": page,
        "state_filter": state_filter,
        "category": category,
    }


def test_cache_miss_guarda_y_hit_evitar_repetir_selector(
    api_client, public_report_factory, monkeypatch
):
    public_report_factory()
    first = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX})

    def selector_must_not_run(_params):
        raise AssertionError("un hit no debe consultar reportes")

    monkeypatch.setattr("apps.map_app.views.public_reports", selector_must_not_run)
    second = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_cache_usa_ttl_exacto(monkeypatch, settings):
    calls = []
    monkeypatch.setattr(
        "apps.map_app.cache.cache.set",
        lambda key, value, timeout=None: calls.append((key, value, timeout)),
    )

    set_public_list("clave", {"results": []})

    assert settings.PUBLIC_MAP_CACHE_TTL_SECONDS == 60
    assert calls == [("clave", {"results": []}, 60)]


def test_key_material_normaliza_bbox_e_incluye_host_pagina_estado_y_categoria(category, settings):
    settings.ALLOWED_HOSTS = ["mapa.example.test", "otro.example.test"]
    cache.set(GENERATION_KEY, "generacion-fija", timeout=None)
    factory = APIRequestFactory()
    request_a = factory.get(LIST_URL, HTTP_HOST="mapa.example.test")
    request_b = factory.get(LIST_URL, HTTP_HOST="mapa.example.test")
    request_other_host = factory.get(LIST_URL, HTTP_HOST="otro.example.test")

    first = build_public_list_cache_key(
        request_a,
        _params("-57.63595,-25.26395,-57.63575,-25.26375", category=category),
    )
    same_cell = build_public_list_cache_key(
        request_b,
        _params("-57.63585,-25.26385,-57.63555,-25.26355", category=category),
    )

    assert first == same_cell
    assert first != build_public_list_cache_key(
        request_a, _params(PUBLIC_BBOX, page=2, category=category)
    )
    assert first != build_public_list_cache_key(
        request_a, _params(PUBLIC_BBOX, state_filter="all", category=category)
    )
    assert first != build_public_list_cache_key(
        request_other_host, _params("-57.63595,-25.26395,-57.63575,-25.26375", category=category)
    )
    assert "-57." not in first


def test_generation_inicial_es_no_vacia_y_estable(category):
    cache.delete(GENERATION_KEY)
    request = APIRequestFactory().get(LIST_URL)

    first = build_public_list_cache_key(request, _params(category=category))
    generation = cache.get(GENERATION_KEY)
    second = build_public_list_cache_key(request, _params(category=category))

    assert first == second
    assert generation
    assert uuid.UUID(generation).hex == generation


def test_invalidaciones_concurrentes_siempre_dejan_token_valido():
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: invalidate_public_reports_cache(), range(32)))

    generation = cache.get(GENERATION_KEY)
    assert generation
    assert uuid.UUID(generation).hex == generation


def test_cache_solo_recibe_payload_publico_redondeado(
    api_client, public_report_factory, monkeypatch
):
    public_report_factory(description="Dato sintético cacheable.")
    captured = []
    monkeypatch.setattr(
        "apps.map_app.views.set_public_list", lambda key, payload: captured.append((key, payload))
    )

    response = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX})

    assert response.status_code == 200
    payload_text = json.dumps(captured[0][1])
    assert '"longitude": -57.636' in payload_text
    assert "location" not in payload_text
    assert "citizen" not in payload_text


def test_invalidacion_rota_generacion_y_hace_visible_el_cambio(api_client, public_report_factory):
    report = public_report_factory()
    first = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX})
    first_generation = cache.get(GENERATION_KEY)

    report.status = ReportStatus.RECHAZADO
    report.save(update_fields=["status", "updated_at"])
    invalidate_public_reports_cache()
    second_generation = cache.get(GENERATION_KEY)
    second = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX})

    assert first.json()["count"] == 1
    assert second.json()["count"] == 0
    assert first_generation != second_generation


def test_fallo_redis_degrada_a_db_sin_loggear_bbox(
    api_client, public_report_factory, monkeypatch, caplog
):
    public_report_factory()
    raw_bbox = "-57.7,-25.35,-57.5,-25.2"

    def redis_failure(*args, **kwargs):
        raise RedisError("fallo simulado")

    monkeypatch.setattr("apps.map_app.cache.cache.get", redis_failure)
    monkeypatch.setattr("apps.map_app.views.PublicReportListView.throttle_classes", [])

    response = api_client.get(LIST_URL, {"bbox": raw_bbox})

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert raw_bbox not in caplog.text
    assert "fallo simulado" not in caplog.text


@pytest.mark.parametrize("operation", ["add", "set"])
def test_fallo_redis_en_generation_o_escritura_no_causa_500(
    operation, api_client, public_report_factory, monkeypatch
):
    public_report_factory()
    monkeypatch.setattr("apps.map_app.views.PublicReportListView.throttle_classes", [])
    if operation == "add":
        cache.delete(GENERATION_KEY)
        monkeypatch.setattr(
            "apps.map_app.cache.cache.add",
            lambda *args, **kwargs: (_ for _ in ()).throw(RedisError()),
        )
    else:
        cache.set(GENERATION_KEY, "generacion-valida", timeout=None)
        monkeypatch.setattr(
            "apps.map_app.cache.cache.set",
            lambda *args, **kwargs: (_ for _ in ()).throw(RedisError()),
        )

    response = api_client.get(LIST_URL, {"bbox": PUBLIC_BBOX})

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_host_no_permitido_es_rechazado_antes_de_construir_cache_key(
    api_client, public_report_factory, settings
):
    public_report_factory()
    settings.ALLOWED_HOSTS = ["mapa.example.test"]

    response = api_client.get(
        LIST_URL,
        {"bbox": PUBLIC_BBOX},
        HTTP_HOST="host-manipulado.example.test",
    )

    assert response.status_code == 400


def test_helper_de_lectura_atrapa_error_redis(monkeypatch):
    monkeypatch.setattr(
        "apps.map_app.cache.cache.get", lambda key: (_ for _ in ()).throw(RedisError())
    )

    assert get_public_list("clave") is None


def test_detalle_no_toca_cache(api_client, public_report_factory, monkeypatch):
    report = public_report_factory()
    monkeypatch.setattr(
        "apps.map_app.views.build_public_list_cache_key",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("detalle cacheado")),
    )

    response = api_client.get(f"{LIST_URL}{report.pk}/")

    assert response.status_code == 200
