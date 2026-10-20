"""Lecturas disponibles y escrituras cerradas ante una caída real de Redis (S14-D14)."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.auth_app.enums import VerificationStatus
from apps.map_app.tests.conftest import PUBLIC_BBOX
from apps.reports_app.models import Report

pytestmark = pytest.mark.django_db


def _sin_redis(settings):
    """El cambio de settings reinicia los handlers de caché sin parches de módulos."""
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://127.0.0.1:1/0",
            "OPTIONS": {"socket_connect_timeout": 0.2},
        }
    }


@pytest.mark.parametrize(
    "endpoint", ["map_list", "map_categories", "ranking", "public_metrics", "operator_metrics"]
)
def test_las_lecturas_publicas_responden_sin_redis(settings, operator_user, institution, endpoint):
    _sin_redis(settings)
    client = APIClient()
    urls = {
        "map_list": f"/api/v1/public/reports/?bbox={PUBLIC_BBOX}",
        "map_categories": "/api/v1/public/categories/",
        "ranking": "/api/v1/ranking/monthly/",
        "public_metrics": "/api/v1/public/metrics/",
        "operator_metrics": f"/api/v1/operator/metrics/?institution_id={institution.pk}",
    }
    headers = {
        "map_list": None,
        "map_categories": None,
        "ranking": "private, no-store",
        "public_metrics": "public, max-age=60",
        "operator_metrics": "private, no-store",
    }
    if endpoint == "operator_metrics":
        client.force_authenticate(operator_user)

    response = client.get(urls[endpoint])

    assert response.status_code == 200
    assert response.get("Cache-Control") == headers[endpoint]


def test_las_escrituras_siguen_fallando_cerrado_sin_redis(
    settings, citizen, category, jpeg_bytes, storage_config, fake_storage
):
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])
    _sin_redis(settings)
    client = APIClient(raise_request_exception=False)
    client.force_authenticate(citizen)
    before = Report.objects.count()

    response = client.post(
        "/api/v1/reports/",
        {
            "category": str(category.pk),
            "description": "Reporte sintético con Redis caído.",
            "photo": SimpleUploadedFile("reporte.jpg", jpeg_bytes, content_type="image/jpeg"),
            "lat": "-25.263712345",
            "lng": "-57.635912345",
        },
        format="multipart",
    )

    assert not 200 <= response.status_code < 300
    assert Report.objects.count() == before
    assert fake_storage.uploads == []
