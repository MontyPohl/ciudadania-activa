import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from apps.admin_app.models import AuditLog
from apps.core.enums import ActorType

pytestmark = pytest.mark.django_db

URL = "/api/v1/admin/audit-logs/"


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _entry(*, actor=None, action="report.resolved", actor_type=ActorType.HUMAN, payload=None):
    return AuditLog.objects.create(
        actor_user=actor,
        actor_type=actor_type,
        action=action,
        payload=payload or {},
    )


def test_list_is_private_exact_and_does_not_expose_actor_pii(admin_user):
    admin_user.full_name = "Administradora de Prueba"
    admin_user.save(update_fields=["full_name"])
    report_id = uuid.uuid4()
    institution_id = uuid.uuid4()
    entry = _entry(
        actor=admin_user,
        action="report.validation_reverted",
        payload={
            "report_id": str(report_id),
            "institution_id": str(institution_id),
            "cycle": 1,
            "reason": "Corrección administrativa fundada.",
        },
    )

    response = _client(admin_user).get(URL)

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                "id": str(entry.pk),
                "actor_user_id": str(admin_user.pk),
                "actor_type": ActorType.HUMAN,
                "action": "report.validation_reverted",
                "payload": entry.payload,
                "created_at": response.json()["results"][0]["created_at"],
            }
        ],
    }
    serialized = str(response.json())
    assert admin_user.email not in serialized
    assert admin_user.full_name not in serialized


def test_fixed_page_size_and_total_order_by_created_at_then_id_desc(admin_user):
    entries = [_entry(action=f"test.action.{index:02d}") for index in range(25)]
    same_time = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    AuditLog.objects.filter(pk__in=[entry.pk for entry in entries]).update(created_at=same_time)
    expected = [
        str(entry.pk) for entry in sorted(entries, key=lambda entry: entry.pk, reverse=True)
    ]

    first = _client(admin_user).get(URL)
    second = _client(admin_user).get(URL + "?page=2")

    assert first.status_code == second.status_code == 200
    assert first.json()["count"] == second.json()["count"] == 25
    assert len(first.json()["results"]) == 20
    assert len(second.json()["results"]) == 5
    assert [item["id"] for item in first.json()["results"]] == expected[:20]
    assert [item["id"] for item in second.json()["results"]] == expected[20:]
    assert "page=2" in first.json()["next"]
    assert second.json()["next"] is None
    assert second.json()["previous"] == "http://testserver/api/v1/admin/audit-logs/"


def test_page_size_stays_fixed_when_global_default_changes(admin_user, settings, monkeypatch):
    from rest_framework.pagination import PageNumberPagination

    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "PAGE_SIZE": 2}
    monkeypatch.setattr(PageNumberPagination, "page_size", 2)
    for index in range(3):
        _entry(action=f"test.fixed-page.{index}")

    response = _client(admin_user).get(URL)

    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert len(response.json()["results"]) == 3


def test_out_of_range_page_is_a_stable_invalid_request(admin_user):
    _entry()

    response = _client(admin_user).get(URL + "?page=2")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(
    ("query", "expected_action"),
    [
        ("action=report.closed", "report.closed"),
        ("actor_type=system", "report.closed"),
        ("actor_user_id={actor_id}", "report.resolved"),
        ("report_id={report_id}", "report.resolved"),
        ("institution_id={institution_id}", "report.resolved"),
    ],
)
def test_approved_filters_are_exact(
    query,
    expected_action,
    admin_user,
):
    report_id = uuid.uuid4()
    institution_id = uuid.uuid4()
    _entry(
        actor=admin_user,
        action="report.resolved",
        payload={
            "report_id": str(report_id),
            "institution_id": str(institution_id),
        },
    )
    _entry(
        action="report.closed",
        actor_type=ActorType.SYSTEM,
        payload={"report_id": str(uuid.uuid4()), "institution_id": str(uuid.uuid4())},
    )
    rendered = query.format(
        actor_id=admin_user.pk,
        report_id=report_id,
        institution_id=institution_id,
    )

    response = _client(admin_user).get(URL + "?" + rendered)

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["action"] == expected_action


def test_date_filters_are_inclusive_civil_days_in_asuncion(admin_user):
    local_timezone = ZoneInfo("America/Asuncion")
    before = _entry(action="before")
    first = _entry(action="first")
    last = _entry(action="last")
    after = _entry(action="after")
    AuditLog.objects.filter(pk=before.pk).update(
        created_at=datetime(2026, 9, 10, 23, 59, 59, tzinfo=local_timezone)
    )
    AuditLog.objects.filter(pk=first.pk).update(
        created_at=datetime(2026, 9, 11, 0, 0, tzinfo=local_timezone)
    )
    AuditLog.objects.filter(pk=last.pk).update(
        created_at=datetime(2026, 9, 11, 23, 59, 59, tzinfo=local_timezone)
    )
    AuditLog.objects.filter(pk=after.pk).update(
        created_at=datetime(2026, 9, 12, 0, 0, tzinfo=local_timezone)
    )

    response = _client(admin_user).get(URL + "?created_from=2026-09-11&created_to=2026-09-11")

    assert response.status_code == 200
    assert [item["action"] for item in response.json()["results"]] == ["last", "first"]


@pytest.mark.parametrize(
    "query",
    [
        "unknown=true",
        "page_size=100",
        "action=one&action=two",
        "actor_type=robot",
        "actor_user_id=invalid",
        "report_id=invalid",
        "institution_id=invalid",
        "created_from=ayer",
        "created_to=9999-12-31",
        "created_from=2026-09-12&created_to=2026-09-11",
        "page=0",
    ],
)
def test_query_schema_is_strict_with_stable_error(query, admin_user):
    response = _client(admin_user).get(URL + "?" + query)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_audit_api_has_no_mutation_methods(method, admin_user):
    response = getattr(_client(admin_user), method)(URL, {}, format="json")

    assert response.status_code == 405
    assert response["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("actor_fixture", ["citizen", "operator_user"])
def test_non_admin_roles_are_forbidden(actor_fixture, request):
    actor = request.getfixturevalue(actor_fixture)

    response = _client(actor).get(URL)

    assert response.status_code == 403
    assert response.json()["code"] == "ADMIN_REQUIRED"
    assert response["Cache-Control"] == "private, no-store"


def test_anonymous_and_invalid_jwt_have_stable_authentication_error():
    invalid_client = APIClient()
    invalid_client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

    anonymous = _client().get(URL)
    invalid = invalid_client.get(URL)

    for response in (anonymous, invalid):
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Se requiere autenticación.",
            "code": "AUTHENTICATION_REQUIRED",
        }
        assert response["Cache-Control"] == "private, no-store"
