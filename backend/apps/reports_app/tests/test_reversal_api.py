import pytest
from rest_framework.test import APIClient

from apps.admin_app.models import AuditLog
from apps.core.enums import ReportStatus
from apps.reports_app.models import ReportStatusHistory
from apps.reports_app.services import validation
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _validated(report, operator_user, institution):
    ledger.on_report_created(report)
    validation.validate_report(
        report_id=report.pk,
        operator=operator_user,
        institution=institution,
    )
    report.refresh_from_db()


def _url(report):
    report_id = getattr(report, "pk", report)
    return f"/api/v1/admin/reports/{report_id}/revert-validation/"


def _post(client, report, payload=None, suffix=""):
    return client.post(
        _url(report) + suffix,
        (
            {
                "reason": "Corrección administrativa fundada.",
                "cycle": 1,
            }
            if payload is None
            else payload
        ),
        format="json",
    )


def test_admin_reversal_contract_and_idempotent_replay(
    report,
    operator_user,
    institution,
    admin_user,
):
    _validated(report, operator_user, institution)
    client = _client(admin_user)

    first = _post(client, report)
    counts = (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    )
    repeated = _post(
        client,
        report,
        {"reason": "Motivo alternativo igualmente válido.", "cycle": 1},
    )

    expected = {
        "id": str(report.pk),
        "status": ReportStatus.CLASIFICADO,
        "cycle": 1,
        "institution": {
            "id": str(institution.pk),
            "name": institution.name,
            "slug": institution.slug,
        },
        "validated_at": None,
        "resolved_at": None,
        "closed_at": None,
    }
    assert first.status_code == repeated.status_code == 200
    assert first.json() == repeated.json() == expected
    assert (
        ReportStatusHistory.objects.count(),
        AuditLog.objects.count(),
        RewardTransaction.objects.count(),
    ) == counts
    assert first["Cache-Control"] == repeated["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"cycle": 1},
        {"reason": "Corrección administrativa fundada."},
        {"reason": "demasiado", "cycle": 1, "extra": True},
        {"reason": "corto", "cycle": 1},
        {"reason": 1234567890, "cycle": 1},
        {"reason": "Corrección administrativa fundada.", "cycle": 0},
    ],
)
def test_reversal_body_is_strict(payload, report, admin_user):
    response = _post(_client(admin_user), report, payload)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


def test_reversal_rejects_query_parameters(report, admin_user):
    response = _post(_client(admin_user), report, suffix="?unexpected=true")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


def test_malformed_json_returns_stable_invalid_request(report, admin_user):
    response = _client(admin_user).post(
        _url(report),
        data=b'{"reason":',
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


def test_stale_cycle_and_invalid_state_are_stable_conflicts(
    report,
    operator_user,
    institution,
    admin_user,
):
    _validated(report, operator_user, institution)

    stale = _post(
        _client(admin_user),
        report,
        {"reason": "Corrección administrativa fundada.", "cycle": 2},
    )
    report.status = ReportStatus.RECHAZADO
    report.save(update_fields=["status", "updated_at"])
    invalid = _post(
        _client(admin_user),
        report,
        {"reason": "Corrección administrativa fundada.", "cycle": 1},
    )

    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_REPORT_CYCLE"
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "REPORT_STATE_NOT_ALLOWED"


@pytest.mark.parametrize("actor_fixture", ["citizen", "operator_user"])
def test_non_admin_roles_receive_stable_forbidden(
    actor_fixture,
    request,
    report,
):
    actor = request.getfixturevalue(actor_fixture)

    response = _post(_client(actor), report)

    assert response.status_code == 403
    assert response.json()["code"] == "ADMIN_REQUIRED"
    assert response["Cache-Control"] == "private, no-store"


def test_missing_report_returns_stable_not_found(admin_user):
    import uuid

    response = _post(_client(admin_user), uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["code"] == "REPORT_NOT_FOUND"


def test_anonymous_and_invalid_jwt_use_stable_authentication_contract(report):
    invalid_client = APIClient()
    invalid_client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

    anonymous = _post(_client(), report)
    invalid = _post(invalid_client, report)

    for response in (anonymous, invalid):
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Se requiere autenticación.",
            "code": "AUTHENTICATION_REQUIRED",
        }
        assert response["Cache-Control"] == "private, no-store"
