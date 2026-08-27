import pytest
from django.contrib.gis.geos import Point
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.core.enums import ReportStatus
from apps.map_app.cache import GENERATION_KEY
from apps.reports_app.models import Report

PUBLIC_BBOX = "-57.8,-25.5,-57.3,-25.0"


@pytest.fixture(autouse=True)
def isolate_public_map_cache():
    """Rota solo claves conocidas; LocMem impide cualquier contacto con Redis real."""
    cache.delete(GENERATION_KEY)
    for address in ("127.0.0.1", "198.51.100.10", "198.51.100.11"):
        cache.delete(f"throttle_public_map_{address}")
    yield
    cache.delete(GENERATION_KEY)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def public_report_factory(category):
    counter = 0

    def make(**overrides):
        nonlocal counter
        counter += 1
        defaults = {
            "citizen": None,
            "category": category,
            "institution": category.default_institution,
            "description": f"Dato sintético para prueba {counter}.",
            "status": ReportStatus.CREADO,
            "priority": category.default_priority,
            "location": Point(-57.6359 + counter / 10000, -25.2637, srid=4326),
        }
        defaults.update(overrides)
        if "category" in overrides and "institution" not in overrides:
            defaults["institution"] = overrides["category"].default_institution
        return Report.objects.create(**defaults)

    return make
