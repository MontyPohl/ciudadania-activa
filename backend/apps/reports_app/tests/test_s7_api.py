import uuid
from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import AuditLog
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core.enums import ReportStatus
from apps.reports_app.models import Report, ReportConfirmation, ReportStatusHistory
from apps.reports_app.services.confirmations import confirm_report
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db

SUMMARY_URL = "/api/v1/citizen/summary/"


@pytest.fixture(autouse=True)
def isolated_throttles():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def verified_citizen(citizen):
    citizen.verification_status = VerificationStatus.APROBADO
    citizen.save(update_fields=["verification_status"])
    return citizen


@pytest.fixture
def verified_other(other_citizen):
    other_citizen.verification_status = VerificationStatus.APROBADO
    other_citizen.save(update_fields=["verification_status"])
    return other_citizen


def authenticated(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def report_for(owner, category, index=0, **overrides):
    values = {
        "citizen": owner,
        "category": category,
        "description": f"Reporte de prueba {index}.",
        "location": Point(-57.6359 + index / 10000, -25.2637, srid=4326),
    }
    values.update(overrides)
    return Report.objects.create(**values)


def confirm_url(report):
    return f"/api/v1/reports/{report.id}/confirm/"


def detail_url(report_id):
    return f"/api/v1/reports/{report_id}/"


def history_url(report_id):
    return f"/api/v1/reports/{report_id}/history/"


def test_confirm_201_shape_y_persistencia(report, verified_other):
    response = authenticated(verified_other).post(confirm_url(report))

    assert response.status_code == 201
    assert response.json() == {
        "report_id": str(report.id),
        "confirmed_by_me": True,
        "confirmation_count": 1,
        "reward": {"impact_points": 10, "status": "PENDIENTE"},
    }
    assert ReportConfirmation.objects.count() == 1
    assert RewardTransaction.objects.count() == 1
    assert AuditLog.objects.filter(action="report.confirmed").count() == 1


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("inactive", "REPORT_NOT_ACTIVE"),
        ("self", "SELF_CONFIRMATION_NOT_ALLOWED"),
        ("duplicate", "ALREADY_CONFIRMED"),
    ],
)
def test_confirm_409_codes_exactos(setup, code, report, verified_citizen, verified_other):
    actor = verified_other
    if setup == "inactive":
        report.status = ReportStatus.RESUELTO
        report.save(update_fields=["status", "updated_at"])
    elif setup == "self":
        report.citizen = verified_citizen
        report.save(update_fields=["citizen", "updated_at"])
        actor = verified_citizen
    else:
        confirm_report(report_id=report.id, citizen=actor)

    response = authenticated(actor).post(confirm_url(report))

    assert response.status_code == 409
    assert response.json()["code"] == code


def test_confirm_rechaza_body_desconocido(report, verified_other):
    response = authenticated(verified_other).post(confirm_url(report), {"citizen": "otro"})

    assert response.status_code == 400
    assert "citizen" in response.json()["detail"]
    assert ReportConfirmation.objects.count() == 0


def test_confirm_reporte_inexistente_404(verified_other):
    response = authenticated(verified_other).post(f"/api/v1/reports/{uuid.uuid4()}/confirm/")

    assert response.status_code == 404
    assert ReportConfirmation.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert AuditLog.objects.filter(action="report.confirmed").count() == 0


@pytest.mark.parametrize("endpoint", ["confirm", "detail", "history", "summary"])
def test_endpoints_privados_anonimo_401(endpoint, report):
    urls = {
        "confirm": confirm_url(report),
        "detail": detail_url(report.id),
        "history": history_url(report.id),
        "summary": SUMMARY_URL,
    }
    method = "post" if endpoint == "confirm" else "get"
    assert getattr(APIClient(), method)(urls[endpoint]).status_code == 401


@pytest.mark.parametrize("kind", ["unverified", "operator"])
@pytest.mark.parametrize("endpoint", ["confirm", "detail", "history", "summary"])
def test_endpoints_privados_requieren_ciudadano_verificado(
    kind, endpoint, report, citizen, user_factory
):
    user = (
        citizen
        if kind == "unverified"
        else user_factory(email="operador-s7@example.test", role=UserRole.OPERATOR)
    )
    urls = {
        "confirm": confirm_url(report),
        "detail": detail_url(report.id),
        "history": history_url(report.id),
        "summary": SUMMARY_URL,
    }
    method = "post" if endpoint == "confirm" else "get"
    assert getattr(authenticated(user), method)(urls[endpoint]).status_code == 403


def test_confirm_throttle_31_da_429_sin_fila_extra(report, verified_other, settings, monkeypatch):
    monkeypatch.setitem(
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "report_confirm", "30/hour"
    )
    client = authenticated(verified_other)

    responses = [client.post(confirm_url(report)) for _ in range(31)]

    assert responses[0].status_code == 201
    assert all(response.status_code == 409 for response in responses[1:30])
    assert responses[30].status_code == 429
    assert ReportConfirmation.objects.count() == 1


@pytest.mark.parametrize(
    ("status", "self_report", "already", "can_confirm", "code"),
    [
        (ReportStatus.CREADO, False, False, True, None),
        (ReportStatus.CREADO, True, False, False, "SELF_CONFIRMATION_NOT_ALLOWED"),
        (ReportStatus.CREADO, False, True, False, "ALREADY_CONFIRMED"),
        (ReportStatus.RESUELTO, False, True, False, "REPORT_NOT_ACTIVE"),
    ],
)
def test_private_detail_contexto_sin_side_effects(
    status,
    self_report,
    already,
    can_confirm,
    code,
    report,
    verified_other,
):
    report.status = status
    if self_report:
        report.citizen = verified_other
    report.save(update_fields=["status", "citizen", "updated_at"])
    if already:
        ReportConfirmation.objects.create(report=report, citizen=verified_other)

    before = ReportConfirmation.objects.count()
    response = authenticated(verified_other).get(detail_url(report.id))

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "id",
        "category",
        "institution",
        "description",
        "status",
        "priority",
        "latitude",
        "longitude",
        "created_at",
        "confirmation_count",
        "confirmed_by_me",
        "confirmation_eligibility",
    }
    assert payload["confirmed_by_me"] is already
    assert payload["confirmation_eligibility"] == {
        "can_confirm": can_confirm,
        "code": code,
    }
    assert ReportConfirmation.objects.count() == before
    serialized = str(payload)
    for forbidden in ("citizen", "email", "changed_by", "file_hash", "file_url", "audit"):
        assert forbidden not in serialized


def test_private_detail_rechazado_o_inexistente_404(report, verified_other):
    report.status = ReportStatus.RECHAZADO
    report.save(update_fields=["status", "updated_at"])
    client = authenticated(verified_other)

    assert client.get(detail_url(report.id)).status_code == 404
    assert client.get(detail_url(uuid.uuid4())).status_code == 404


def test_history_owner_only_privado_y_cronologico(verified_citizen, verified_other, category):
    report = report_for(verified_citizen, category)
    newer = ReportStatusHistory.objects.create(
        report=report,
        previous_status=ReportStatus.CLASIFICADO,
        new_status=ReportStatus.ASIGNADO,
        changed_by=verified_other,
        reason="Asignación interna.",
    )
    older = ReportStatusHistory.objects.create(
        report=report,
        previous_status=ReportStatus.CREADO,
        new_status=ReportStatus.CLASIFICADO,
        changed_by=verified_citizen,
        reason="Clasificación.",
    )
    ReportStatusHistory.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(days=1)
    )

    response = authenticated(verified_citizen).get(history_url(report.id))

    assert response.status_code == 200
    assert [row["new_status"] for row in response.json()] == [
        ReportStatus.CLASIFICADO,
        ReportStatus.ASIGNADO,
    ]
    assert all(
        set(row) == {"previous_status", "new_status", "reason", "created_at"}
        for row in response.json()
    )
    assert "changed_by" not in str(response.json())
    assert newer.pk


def test_history_ajeno_e_inexistente_devuelven_mismo_404(report, verified_citizen, verified_other):
    report.citizen = verified_citizen
    report.save(update_fields=["citizen", "updated_at"])
    client = authenticated(verified_other)

    foreign = client.get(history_url(report.id))
    missing = client.get(history_url(uuid.uuid4()))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_summary_claves_estables_conteos_y_solo_impact_points(
    verified_citizen, verified_other, category, institution
):
    created = report_for(verified_citizen, category, 1, status=ReportStatus.CREADO)
    report_for(verified_citizen, category, 2, status=ReportStatus.CREADO)
    report_for(verified_citizen, category, 3, status=ReportStatus.CERRADO)
    foreign = report_for(verified_other, category, 4)
    ReportConfirmation.objects.create(report=foreign, citizen=verified_citizen)

    RewardTransaction.objects.create(
        user=verified_citizen,
        report=created,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=20,
        credit_delta=999,
        status=RewardTxStatus.PENDIENTE,
    )
    RewardTransaction.objects.create(
        user=verified_citizen,
        report=report_for(verified_citizen, category, 5),
        institution=institution,
        event_type=RewardEventType.VALIDATION_BONUS,
        impact_delta=30,
        credit_delta=777,
        status=RewardTxStatus.ACREDITADO,
        credited_at=timezone.now(),
    )

    response = authenticated(verified_citizen).get(SUMMARY_URL)

    assert response.status_code == 200
    payload = response.json()
    assert set(payload["reports_by_status"]) == set(ReportStatus.values)
    assert payload["reports_by_status"][ReportStatus.CREADO] == 3
    assert payload["reports_by_status"][ReportStatus.CERRADO] == 1
    assert payload["reports_by_status"][ReportStatus.RECHAZADO] == 0
    assert payload["confirmations_performed"] == 1
    assert payload["points"] == {"pending": 20, "accredited": 30}
    assert "credit_delta" not in str(payload).lower()


def test_query_budgets_detail_history_summary(
    verified_citizen, verified_other, category, django_assert_num_queries
):
    report = report_for(verified_citizen, category)
    ReportStatusHistory.objects.create(report=report, new_status=ReportStatus.CREADO)

    client = APIClient()
    client.force_authenticate(verified_citizen)
    with django_assert_num_queries(1):
        assert client.get(detail_url(report.id)).status_code == 200
    with django_assert_num_queries(2):
        assert client.get(history_url(report.id)).status_code == 200
    with django_assert_num_queries(3):
        assert client.get(SUMMARY_URL).status_code == 200
