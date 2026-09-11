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
):
    _in_process(report, operator_user, institution)

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
    assert response.json()["signed_url"].startswith("http://testserver/api/v1/reports/evidence-access/")
    assert str(report.pk) not in response.json()["signed_url"]
    assert "resolution-evidence" not in response.json()["signed_url"]
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
):
    _in_process(report, operator_user, institution)
    evidence = _evidence(report, operator_user)

    author_response = _client(citizen).get(f"/api/v1/reports/{report.pk}/evidence/")
    other_response = _client(other_citizen).get(f"/api/v1/reports/{report.pk}/evidence/")
    admin_response = _client(admin_user).get(f"/api/v1/admin/reports/{report.pk}/evidence/")

    assert author_response.status_code == admin_response.status_code == 200
    assert author_response.json() == admin_response.json()
    assert author_response.json()[0]["id"] == str(evidence.pk)
    assert other_response.status_code == 404
    for response in (author_response, other_response, admin_response):
        assert response["Cache-Control"] == "private, no-store"


def test_signed_access_url_proxies_bytes_without_exposing_object_key(
    report,
    operator_user,
    institution,
    citizen,
    storage_config,
    monkeypatch,
):
    _in_process(report, operator_user, institution)
    evidence = _evidence(report, operator_user)
    downloads = []

    def download(bucket, key):
        downloads.append((bucket, key))
        return b"normalized-image"

    monkeypatch.setattr(storage, "download_private", download)
    listing = _client(citizen).get(f"/api/v1/reports/{report.pk}/evidence/")
    signed_url = listing.json()[0]["signed_url"]

    response = APIClient().get(signed_url)

    assert response.status_code == 200
    assert response.content == b"normalized-image"
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, no-store"
    assert evidence.evidence_url not in signed_url
    assert str(report.pk) not in signed_url
    assert downloads == [(storage.reports_bucket(), evidence.evidence_url)]


@pytest.mark.parametrize("mode", ["demoted", "inactive_institution"])
def test_operator_get_revalidates_role_and_active_institution(
    mode,
    report,
    operator_user,
    institution,
):
    from apps.auth_app.enums import UserRole

    _in_process(report, operator_user, institution)
    _evidence(report, operator_user)
    if mode == "demoted":
        operator_user.role = UserRole.CITIZEN
        operator_user.save(update_fields=["role"])
    else:
        institution.is_active = False
        institution.save(update_fields=["is_active", "updated_at"])

    response = _client(operator_user).get(_operator_url(report, institution))

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"
    assert response["Cache-Control"] == "private, no-store"


def test_private_evidence_get_rejects_unknown_query_with_stable_code(
    report,
    citizen,
):
    response = _client(citizen).get(
        f"/api/v1/reports/{report.pk}/evidence/?unexpected=true"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


def test_admin_evidence_get_uses_stable_auth_codes(report, citizen):
    anonymous = APIClient().get(f"/api/v1/admin/reports/{report.pk}/evidence/")
    wrong_role = _client(citizen).get(f"/api/v1/admin/reports/{report.pk}/evidence/")

    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert wrong_role.status_code == 403
    assert wrong_role.json()["code"] == "ADMIN_REQUIRED"


@pytest.mark.parametrize(
    "url_factory",
    [
        lambda report, institution: f"/api/v1/reports/{report.pk}/evidence/",
        lambda report, institution: f"/api/v1/admin/reports/{report.pk}/evidence/",
        lambda report, institution: _operator_url(report, institution),
    ],
)
def test_invalid_jwt_uses_stable_authentication_code(url_factory, report, institution):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

    response = client.get(url_factory(report, institution))

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Se requiere autenticación.",
        "code": "AUTHENTICATION_REQUIRED",
    }
    assert response["Cache-Control"] == "private, no-store"
