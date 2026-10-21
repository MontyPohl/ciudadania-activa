import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework import serializers

from apps.core.enums import ReportStatus
from apps.core.services import storage
from apps.map_app.serializers import PublicReportDetailSerializer, PublicReportSerializer
from apps.map_app.tests.conftest import PUBLIC_BBOX
from apps.reports_app.models import ReportMedia, ResolutionEvidence

pytestmark = pytest.mark.django_db
BASE = "/api/v1/public/reports/"


@pytest.fixture(autouse=True)
def isolate_media_throttle():
    cache.delete("throttle_public_report_media_127.0.0.1")


def photo(report, suffix="jpg"):
    media_id = uuid.uuid4()
    return ReportMedia.objects.create(
        id=media_id,
        report=report,
        file_url=f"reports/{report.pk}/{media_id}.{suffix}",
        file_hash=uuid.uuid4().hex,
    )


def evidence(report):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Evidencia sintética",
        evidence_url="resolution-evidence/test.png",
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="a" * 64,
    )


@pytest.mark.parametrize("status", ReportStatus.values)
def test_publication_states(api_client, public_report_factory, storage_config, status):
    report = public_report_factory(status=status)
    media = photo(report)
    item = evidence(report)
    photo_allowed = status in ("ASIGNADO", "EN_PROCESO", "RESUELTO", "CERRADO")
    evidence_allowed = status in ("RESUELTO", "CERRADO")
    photo_url = f"{BASE}{report.pk}/photo/"
    evidence_url = f"{BASE}{report.pk}/evidence/{item.pk}/"
    with patch("apps.map_app.media_views.storage.download_private", return_value=b"pixels") as read:
        for url, allowed, key in [
            (photo_url, photo_allowed, media.file_url),
            (evidence_url, evidence_allowed, item.evidence_url),
        ]:
            read.reset_mock()
            response = api_client.get(url, HTTP_AUTHORIZATION="Bearer invalid")
            assert response.status_code == (200 if allowed else 404)
            assert response["Cache-Control"] == "no-store"
            assert response["X-Content-Type-Options"] == "nosniff"
            if allowed:
                read.assert_called_once_with("reportes", key)
                assert response.content == b"pixels"
                assert response["Content-Type"] == (
                    "image/png" if key.endswith("png") else "image/jpeg"
                )
            else:
                read.assert_not_called()
    assert PublicReportSerializer(report).data["photo_url"] == (
        photo_url if photo_allowed else None
    )
    detail = PublicReportDetailSerializer(report).data
    assert detail["resolution_evidences"] == (
        [
            {
                "id": str(item.pk),
                "evidence_url": evidence_url,
                "created_at": serializers.DateTimeField().to_representation(item.created_at),
            }
        ]
        if evidence_allowed
        else []
    )


def test_missing_and_revoked_are_uniform(api_client, public_report_factory, storage_config):
    report = public_report_factory(status=ReportStatus.ASIGNADO)
    media = photo(report)
    url = f"{BASE}{report.pk}/photo/"
    with patch("apps.map_app.media_views.storage.download_private", return_value=b"pixels") as read:
        assert api_client.get(url).status_code == 200
        report.status = ReportStatus.RECHAZADO
        report.save(update_fields=["status"])
        read.reset_mock()
        revoked = api_client.get(url)
        missing = api_client.get(f"{BASE}{uuid.uuid4()}/photo/")
        assert revoked.status_code == missing.status_code == 404
        assert revoked.json() == missing.json() == {"detail": "No se encontró la imagen."}
        read.assert_not_called()
    report.status = ReportStatus.ASIGNADO
    report.save(update_fields=["status"])
    with patch(
        "apps.map_app.media_views.storage.download_private",
        side_effect=storage.StorageObjectNotFoundError(),
    ):
        assert api_client.get(url).json() == missing.json()
    media.delete()
    assert api_client.get(url).json() == missing.json()
    assert PublicReportSerializer(report).data["photo_url"] is None


def test_first_photo_and_untrusted_query(api_client, public_report_factory, storage_config):
    report = public_report_factory(status=ReportStatus.ASIGNADO)
    first = photo(report, "png")
    photo(report)
    with patch("apps.map_app.media_views.storage.download_private", return_value=b"pixels") as read:
        response = api_client.get(f"{BASE}{report.pk}/photo/?key=identity/selfie.jpg&token=private")
        assert response.status_code == 200
        read.assert_called_once_with("reportes", first.file_url)


def test_evidence_cannot_cross_reports(api_client, public_report_factory, storage_config):
    report = public_report_factory(status=ReportStatus.RESUELTO)
    other = public_report_factory(status=ReportStatus.RESUELTO)
    item = evidence(report)
    with patch("apps.map_app.media_views.storage.download_private") as read:
        response = api_client.get(f"{BASE}{other.pk}/evidence/{item.pk}/")
        assert response.status_code == 404
        read.assert_not_called()


def test_storage_unavailable_is_generic(api_client, public_report_factory, storage_config):
    report = public_report_factory(status=ReportStatus.ASIGNADO)
    photo(report)
    with patch(
        "apps.map_app.media_views.storage.download_private",
        side_effect=storage.StorageUnavailableError("secret internal key"),
    ):
        response = api_client.get(f"{BASE}{report.pk}/photo/")
    assert response.status_code == 503
    assert response.json() == {"detail": "No se pudo obtener la imagen en este momento."}
    assert response["Cache-Control"] == "no-store"


def test_media_scope_is_independent(api_client, public_report_factory, settings, monkeypatch):
    monkeypatch.setitem(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "public_report_media", "1/min"
    )
    report = public_report_factory()
    url = f"{BASE}{report.pk}/photo/"
    assert api_client.get(url).status_code == 404
    blocked = api_client.get(url)
    assert blocked.status_code == 429
    assert blocked["Cache-Control"] == "no-store"
    assert api_client.get(f"{BASE}{report.pk}/").status_code == 200


def test_list_photo_query_count_is_bounded(
    api_client, public_report_factory, django_assert_num_queries
):
    for _ in range(3):
        photo(public_report_factory(status=ReportStatus.ASIGNADO))
    with django_assert_num_queries(2):
        response = api_client.get(BASE, {"bbox": PUBLIC_BBOX})
    assert response.status_code == 200
    assert all(item["photo_url"] for item in response.json()["results"])
