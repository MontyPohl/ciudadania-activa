import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.admin_app.models import AuditLog, Institution, InstitutionOperator
from apps.auth_app.enums import UserRole
from apps.core.enums import ReportStatus
from apps.reports_app.models import ReportStatusHistory, ResolutionEvidence
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _validated(report, operator_user, institution):
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


def _evidence(report, operator_user):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo concluido.",
        evidence_url=f"resolution-evidence/{report.pk}/1/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=1,
        idempotency_key=uuid.uuid4(),
        content_hash="b" * 64,
    )


def _url(report, institution):
    report_id = getattr(report, "pk", report)
    institution_id = getattr(institution, "pk", institution)
    return f"/api/v1/operator/reports/{report_id}/status/?institution_id={institution_id}"


def _patch(client, report, institution, status, cycle=1):
    return client.patch(
        _url(report, institution),
        {"status": status, "expected_cycle": cycle},
        format="json",
    )


def test_full_operator_lifecycle_response_and_idempotent_close(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    client = _client(operator_user)

    processing = _patch(client, report, institution, ReportStatus.EN_PROCESO)
    _evidence(report, operator_user)
    resolved = _patch(client, report, institution, ReportStatus.RESUELTO)
    closed = _patch(client, report, institution, ReportStatus.CERRADO)
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )
    repeated = _patch(client, report, institution, ReportStatus.CERRADO)

    assert processing.status_code == resolved.status_code == closed.status_code == 200
    assert repeated.status_code == 200
    assert processing.json()["status"] == ReportStatus.EN_PROCESO
    assert resolved.json()["status"] == ReportStatus.RESUELTO
    assert closed.json()["status"] == repeated.json()["status"] == ReportStatus.CERRADO
    assert processing.json()["resolved_at"] is None
    assert resolved.json()["resolved_at"] is not None
    assert closed.json()["closed_at"] is not None
    assert repeated.json() == closed.json()
    assert set(closed.json()) == {
        "id",
        "status",
        "cycle",
        "institution",
        "validated_at",
        "resolved_at",
        "closed_at",
    }
    assert closed.json()["cycle"] == 1
    assert closed.json()["institution"] == {
        "id": str(institution.pk),
        "name": institution.name,
        "slug": institution.slug,
    }
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts
    for response in (processing, resolved, closed, repeated):
        assert response["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(
    ("payload", "suffix"),
    [
        ({"status": ReportStatus.EN_PROCESO}, ""),
        (
            {
                "status": ReportStatus.EN_PROCESO,
                "expected_cycle": 1,
                "unexpected": True,
            },
            "",
        ),
        ({"status": ReportStatus.EN_PROCESO, "expected_cycle": 1}, "&other=true"),
        ({"status": ReportStatus.EN_PROCESO, "expected_cycle": 1}, "&institution_id=x"),
    ],
)
def test_status_schema_is_strict_with_stable_error(
    payload,
    suffix,
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)

    response = _client(operator_user).patch(
        _url(report, institution) + suffix,
        payload,
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


def test_status_requires_valid_single_institution_context(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    base = f"/api/v1/operator/reports/{report.pk}/status/"
    payload = {"status": ReportStatus.EN_PROCESO, "expected_cycle": 1}

    missing = _client(operator_user).patch(base, payload, format="json")
    malformed = _client(operator_user).patch(
        base + "?institution_id=not-a-uuid",
        payload,
        format="json",
    )

    for response in (missing, malformed):
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_REQUEST"
        assert response["Cache-Control"] == "private, no-store"


def test_stale_cycle_and_missing_evidence_return_stable_conflicts(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    client = _client(operator_user)

    stale = _patch(client, report, institution, ReportStatus.EN_PROCESO, cycle=2)
    processing = _patch(client, report, institution, ReportStatus.EN_PROCESO)
    missing = _patch(client, report, institution, ReportStatus.RESUELTO)

    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_REPORT_CYCLE"
    assert processing.status_code == 200
    assert missing.status_code == 409
    assert missing.json()["code"] == "RESOLUTION_EVIDENCE_REQUIRED"


def test_cross_tenant_and_missing_share_not_found_contract(
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    other = Institution.objects.get(slug="ande")
    InstitutionOperator.objects.create(
        user=operator_user,
        institution=other,
        active=True,
    )
    missing = uuid.uuid4()

    cross_tenant = _patch(
        _client(operator_user),
        report,
        other,
        ReportStatus.EN_PROCESO,
    )
    absent = _patch(
        _client(operator_user),
        missing,
        institution,
        ReportStatus.EN_PROCESO,
    )

    assert cross_tenant.status_code == absent.status_code == 404
    assert cross_tenant.json()["code"] == absent.json()["code"] == "REPORT_NOT_FOUND"


@pytest.mark.parametrize("role", [UserRole.CITIZEN, UserRole.ADMIN])
def test_non_operator_roles_receive_stable_forbidden(
    role,
    user_factory,
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    user = user_factory(email=f"{role}@example.com", role=role)

    response = _patch(_client(user), report, institution, ReportStatus.EN_PROCESO)

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("inactive", ["membership", "institution"])
def test_inactive_operator_context_is_forbidden(
    inactive,
    report,
    operator_user,
    institution,
):
    _validated(report, operator_user, institution)
    if inactive == "membership":
        membership = InstitutionOperator.objects.get(
            user=operator_user,
            institution=institution,
        )
        membership.active = False
        membership.save(update_fields=["active", "updated_at"])
    else:
        institution.is_active = False
        institution.save(update_fields=["is_active", "updated_at"])

    response = _patch(
        _client(operator_user),
        report,
        institution,
        ReportStatus.EN_PROCESO,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"
    assert response["Cache-Control"] == "private, no-store"


def test_invalid_jwt_and_anonymous_use_stable_authentication_contract(report, institution):
    invalid_client = APIClient()
    invalid_client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

    anonymous = _patch(_client(), report, institution, ReportStatus.EN_PROCESO)
    invalid = _patch(invalid_client, report, institution, ReportStatus.EN_PROCESO)

    for response in (anonymous, invalid):
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Se requiere autenticación.",
            "code": "AUTHENTICATION_REQUIRED",
        }
        assert response["Cache-Control"] == "private, no-store"
