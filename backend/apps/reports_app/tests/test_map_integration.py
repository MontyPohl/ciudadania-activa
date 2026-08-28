import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.auth_app.enums import VerificationStatus
from apps.reports_app.services.reports import create_report

pytestmark = pytest.mark.django_db

PUBLIC_REPORTS_URL = "/api/v1/public/reports/"
PUBLIC_BBOX = "-57.8,-25.5,-57.3,-25.0"


def test_create_invalida_generacion_y_creado_aparece_en_mapa_con_privacidad(
    citizen,
    category,
    jpeg_bytes,
    storage_config,
    fake_storage,
    django_capture_on_commit_callbacks,
):
    cache.clear()
    api = APIClient()
    before = api.get(PUBLIC_REPORTS_URL, {"bbox": PUBLIC_BBOX})
    assert before.status_code == 200
    assert before.json()["count"] == 0

    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])
    photo = SimpleUploadedFile("photo.jpg", jpeg_bytes, content_type="image/jpeg")
    with django_capture_on_commit_callbacks(execute=True):
        result = create_report(
            citizen=citizen,
            category=category,
            description="Descripción pública segura.",
            photo=photo,
            latitude=-25.263712345,
            longitude=-57.635912345,
        )

    after = api.get(PUBLIC_REPORTS_URL, {"bbox": PUBLIC_BBOX})

    assert after.status_code == 200
    assert after.json()["count"] == 1
    public = after.json()["results"][0]
    assert public["id"] == str(result.report.id)
    assert public["latitude"] == -25.264
    assert public["longitude"] == -57.636
    assert "citizen" not in public
    assert "file_url" not in public
