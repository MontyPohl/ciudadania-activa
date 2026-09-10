import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.enums import ReportStatus
from apps.core.services import storage
from apps.reports_app.models import ResolutionEvidence

pytestmark = pytest.mark.django_db


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _in_process(report, operator_user, institution):
    report.status = ReportStatus.ASIGNADO
    report.institution = institution
    report.validated_at = timezone.now()
    report.validated_by = operator_user
    report.resolution_cycle = 1
    report.save(
        update_fields=[
            "status",
            "institution",
            "validated_at",
            "validated_by",
            "resolution_cycle",
            "updated_at",
        ]
    )
    report.status = ReportStatus.EN_PROCESO
    report.save(update_fields=["status", "updated_at"])


def _operator_url(report, institution):
    return f"/api/v1/operator/reports/{report.pk}/evidence/?institution_id={institution.pk}"


def _evidence(report, operator_user):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo terminado.",
        evidence_url=f"resolution-evidence/{report.pk}/1/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="a" * 64,
    )


def test_operator_post_creates_private_evidence_contract(
    report,
    operator_user,
    institution,
    storage_config,
    fake_storage,
    selfie_factory,
    jpeg_bytes,
    monkeypatch,
):
    _in_process(report, operator_user, institution)
    monkeypatch.setattr(storage, "create_signed_url", lambda *args, **kwargs: "https://signed.test")

    response = _client(operator_user).post(
        _operator_url(report, institution),
        {
            "file": selfie_factory(jpeg_bytes, name="evidence.jpg"),
            "description": "Trabajo terminado.",
            "cycle": 1,
        },
        format="multipart",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )

    assert response.status_code == 201
    assert response["Cache-Control"] == "private, no-store"
    assert set(response.json()) == {
        "id",
        "cycle",
        "description",
        "created_at",
        "signed_url",
        "expires_in",
    }
    assert response.json()["cycle"] == 1
    assert response.json()["signed_url"] == "https://signed.test"
    assert response.json()["expires_in"] == 300
    assert "evidence_url" not in response.json()
    assert "content_hash" not in response.json()


def test_operator_post_requires_uuid_idempotency_key(
    report,
    operator_user,
    institution,
    selfie_factory,
    jpeg_bytes,
):
    _in_process(report, operator_user, institution)

    response = _client(operator_user).post(
        _operator_url(report, institution),
        {"file": selfie_factory(jpeg_bytes), "description": "Lista.", "cycle": 1},
        format="multipart",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


def test_operator_cross_tenant_is_indistinguishable_from_missing(
    report,
    operator_user,
    institution,
    storage_config,
    selfie_factory,
    jpeg_bytes,
):
    from apps.admin_app.models import Institution

    other = Institution.objects.get(slug="ande")
    _in_process(report, operator_user, other)

    response = _client(operator_user).post(
        _operator_url(report, institution),
        {"file": selfie_factory(jpeg_bytes), "description": "Lista.", "cycle": 1},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "REPORT_NOT_FOUND"


def test_author_and_admin_can_list_but_other_citizen_cannot(
    report,
    operator_user,
    institution,
    citizen,
    other_citizen,
    admin_user,
    storage_config,
    monkeypatch,
):
    _in_process(report, operator_user, institution)
    evidence = _evidence(report, operator_user)
    monkeypatch.setattr(
        storage,
        "create_signed_url",
        lambda bucket, key, expires_in: f"https://signed.test/{evidence.pk}",
    )

    author_response = _client(citizen).get(f"/api/v1/reports/{report.pk}/evidence/")
    other_response = _client(other_citizen).get(f"/api/v1/reports/{report.pk}/evidence/")
    admin_response = _client(admin_user).get(f"/api/v1/admin/reports/{report.pk}/evidence/")

    assert author_response.status_code == admin_response.status_code == 200
    assert author_response.json() == admin_response.json()
    assert author_response.json()[0]["id"] == str(evidence.pk)
    assert other_response.status_code == 404
    for response in (author_response, other_response, admin_response):
        assert response["Cache-Control"] == "private, no-store"
