"""Contrato HTTP y privacidad del API de recompensas de Sprint 12."""

import uuid
from unittest.mock import Mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.admin_app.models import Institution
from apps.auth_app.enums import VerificationStatus
from apps.reports_app.models import ReportConfirmation
from apps.rewards_app import periods
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction
from apps.rewards_app.services import redemptions

pytestmark = pytest.mark.django_db

INSTITUTION_KEYS = {"id", "name", "slug"}
REWARD_PUBLIC_KEYS = {"id", "name", "description", "credit_cost", "simulated"}
REDEMPTION_KEYS = {
    "id",
    "code",
    "period",
    "credits_spent",
    "simulated",
    "created_at",
    "reward",
    "institution",
}
OPERATOR_REWARD_KEYS = {
    "id",
    "institution",
    "name",
    "description",
    "active",
    "credit_cost",
    "simulated",
    "created_at",
    "updated_at",
}
PAGINATION_KEYS = {"count", "next", "previous", "results"}


@pytest.fixture
def verified_citizen(user_factory):
    return user_factory(
        email="canje-api-s12@example.test",
        full_name="María González",
        verification_status=VerificationStatus.APROBADO,
    )


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _private(response):
    assert response["Cache-Control"] == "private, no-store"
    return response.json()


@pytest.mark.parametrize(
    ("actor", "method", "endpoint", "expected_status"),
    [
        ("anonymous", "get", "summary", 401),
        ("anonymous", "post", "redeem", 401),
        ("unverified", "get", "catalog", 403),
        ("unverified", "post", "redeem", 403),
        ("operator", "post", "redeem", 403),
        ("citizen", "get", "operator", 403),
        ("operator", "patch", "foreign_reward", 403),
        ("operator", "get", "foreign_list", 403),
        ("operator", "get", "foreign_redemptions", 403),
    ],
)
def test_permisos(
    actor, method, endpoint, expected_status, citizen, verified_citizen, operator_user, institution
):
    target = institution
    if endpoint in {"foreign_reward", "foreign_list", "foreign_redemptions"}:
        target = Institution.objects.create(name="Otra institución", slug="otra-api-s12")
    reward = Reward.objects.create(institution=target, name="Vale simulado")
    users = {
        "anonymous": None,
        "unverified": citizen,
        "operator": operator_user,
        "citizen": verified_citizen,
    }
    urls = {
        "summary": "/api/v1/rewards/summary/",
        "catalog": "/api/v1/rewards/catalog/",
        "redeem": f"/api/v1/rewards/{reward.pk}/redeem/",
        "operator": "/api/v1/operator/rewards/",
        "foreign_reward": f"/api/v1/operator/rewards/{reward.pk}/",
        "foreign_list": f"/api/v1/operator/rewards/?institution_id={target.pk}",
        "foreign_redemptions": f"/api/v1/operator/rewards/redemptions/?institution_id={target.pk}",
    }
    client = _client(users[actor])
    response = getattr(client, method)(urls[endpoint])

    assert response.status_code == expected_status
    payload = _private(response)
    if endpoint in {"operator", "foreign_reward", "foreign_list", "foreign_redemptions"}:
        assert payload == {
            "detail": "Se requiere ser operador de la institución solicitada.",
            "code": "INSTITUTION_OPERATOR_REQUIRED",
        }


def test_redeem_contrato_201_y_409(verified_citizen, institution, monkeypatch):
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    RewardTransaction.objects.create(
        user=verified_citizen,
        institution=institution,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=1130,
        credit_delta=1130,
        status=RewardTxStatus.ACREDITADO,
        credited_at=timezone.now(),
    )
    client = _client(verified_citizen)
    url = f"/api/v1/rewards/{reward.pk}/redeem/"
    response = client.generic("POST", url)

    assert response.status_code == 201
    payload = _private(response)
    assert set(payload) == {"redemption", "balance_after"}
    voucher = payload["redemption"]
    assert set(voucher) == REDEMPTION_KEYS
    redemption = RewardRedemption.objects.get(pk=voucher["id"])
    assert voucher == {
        "id": str(redemption.pk),
        "code": redemption.code,
        "period": periods.format_period(redemption.period_month),
        "credits_spent": 1000,
        "simulated": True,
        "created_at": voucher["created_at"],
        "reward": {"id": str(reward.pk), "name": reward.name},
        "institution": {
            "id": str(institution.pk),
            "name": institution.name,
            "slug": institution.slug,
        },
    }
    assert isinstance(voucher["created_at"], str)
    assert payload["balance_after"] == 130

    response = client.generic("POST", url)
    assert response.status_code == 409
    assert _private(response) == {
        "detail": f"Ya canjeaste la recompensa de {institution.name} este mes.",
        "code": "MONTHLY_LIMIT_REACHED",
        "redemption_id": str(redemption.pk),
    }
    for field in ("cost", "credit_cost"):
        response = client.post(url, {field: 1}, format="json")
        assert response.status_code == 400
        assert _private(response) == {"detail": {field: ["Este campo no está permitido."]}}

    # Se prueba la traducción HTTP de los conflictos, sin repetir sus reglas de dominio.
    for conflict, expected in [
        (
            redemptions.RedemptionConflict("REWARD_INACTIVE"),
            {"detail": "La recompensa no está disponible.", "code": "REWARD_INACTIVE"},
        ),
        (
            redemptions.RedemptionConflict(
                "INSUFFICIENT_BALANCE", balance=130, missing_credits=870
            ),
            {
                "detail": f"Te faltan 870 créditos de {institution.name} para canjear.",
                "code": "INSUFFICIENT_BALANCE",
                "balance": 130,
                "missing_credits": 870,
            },
        ),
    ]:
        monkeypatch.setattr(redemptions, "redeem", Mock(side_effect=conflict))
        response = client.generic("POST", url)
        assert response.status_code == 409
        assert _private(response) == expected


def test_operador_crea_ignorando_costo(operator_user, institution):
    client = _client(operator_user)
    url = f"/api/v1/operator/rewards/?institution_id={institution.pk}"
    response = client.get(url)
    assert response.status_code == 200
    assert _private(response) == {
        "institution": {
            "id": str(institution.pk),
            "name": institution.name,
            "slug": institution.slug,
        },
        "reward": None,
    }
    response = client.post(
        url,
        {
            "name": "  Vale simulado  ",
            "description": "Descripción",
            "credit_cost": 5,
            "simulated": False,
        },
        format="json",
    )
    assert response.status_code == 201
    payload = _private(response)
    assert set(payload) == OPERATOR_REWARD_KEYS
    assert set(payload["institution"]) == INSTITUTION_KEYS
    assert payload["credit_cost"] == 1000
    assert payload["simulated"] is True
    assert payload["active"] is True
    assert payload["name"] == "Vale simulado"
    reward_id = payload["id"]

    response = client.post(url, {"name": "Otra recompensa"}, format="json")
    assert response.status_code == 409
    assert _private(response) == {"code": "REWARD_ALREADY_EXISTS", "reward_id": reward_id}

    response = client.patch(
        f"/api/v1/operator/rewards/{reward_id}/?institution_id={institution.pk}",
        {"active": False, "credit_cost": 7, "simulated": False},
        format="json",
    )
    assert response.status_code == 200
    payload = _private(response)
    assert set(payload) == OPERATOR_REWARD_KEYS
    assert payload["credit_cost"] == 1000
    assert payload["simulated"] is True
    assert payload["active"] is False
    assert payload["name"] == "Vale simulado"


def test_privacidad_respuestas(verified_citizen, other_citizen, institution, operator_user, report):
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    now = timezone.now()
    period = periods.current_period(now)
    own, foreign = (
        RewardRedemption.objects.create(
            user=user,
            reward=reward,
            institution=institution,
            period_month=period,
            code=f"CA-0000-0000-0000-000{index}",
        )
        for index, user in enumerate((verified_citizen, other_citizen), start=1)
    )
    RewardTransaction.objects.create(
        user=verified_citizen,
        institution=institution,
        redemption=own,
        event_type=RewardEventType.REDEMPTION,
        impact_delta=0,
        credit_delta=-1000,
        status=RewardTxStatus.ACREDITADO,
        credited_at=now,
    )
    confirmation = ReportConfirmation.objects.create(report=report, citizen=verified_citizen)
    RewardTransaction.objects.create(
        user=verified_citizen,
        confirmation=confirmation,
        event_type=RewardEventType.CONFIRMATION_CREATED,
        impact_delta=10,
        credit_delta=10,
        status=RewardTxStatus.PENDIENTE,
    )
    client = _client(verified_citizen)
    for redemption_id in (foreign.pk, uuid.uuid4()):
        response = client.get(f"/api/v1/rewards/redemptions/{redemption_id}/")
        assert response.status_code == 404
        _private(response)

    response = client.get(f"/api/v1/rewards/redemptions/{own.pk}/")
    assert response.status_code == 200
    assert set(_private(response)) == REDEMPTION_KEYS
    response = client.get("/api/v1/rewards/redemptions/")
    assert response.status_code == 200
    payload = _private(response)
    assert set(payload) == PAGINATION_KEYS
    assert [row["id"] for row in payload["results"]] == [str(own.pk)]
    assert set(payload["results"][0]) == REDEMPTION_KEYS

    response = client.get("/api/v1/rewards/summary/")
    assert response.status_code == 200
    payload = _private(response)
    assert set(payload) == {"impact", "credits_by_institution", "period"}
    assert set(payload["impact"]) == {"historical", "current_month", "pending"}
    assert set(payload["credits_by_institution"][0]) == {"institution", "balance"}
    assert set(payload["credits_by_institution"][0]["institution"]) == INSTITUTION_KEYS
    assert payload["period"] == periods.format_period(period)

    response = client.get("/api/v1/rewards/catalog/")
    assert response.status_code == 200
    payload = _private(response)
    assert set(payload) == {"period", "results"}
    entry = payload["results"][0]
    assert set(entry) == {
        "reward",
        "institution",
        "balance",
        "can_redeem",
        "blocked_reason",
        "missing_credits",
        "redemption_this_month",
    }
    assert set(entry["reward"]) == REWARD_PUBLIC_KEYS
    assert set(entry["institution"]) == INSTITUTION_KEYS
    assert set(entry["redemption_this_month"]) == {"id", "code", "created_at"}

    response = client.get("/api/v1/rewards/transactions/")
    assert response.status_code == 200
    payload = _private(response)
    assert set(payload) == PAGINATION_KEYS
    assert len(payload["results"]) == 2
    for movement in payload["results"]:
        assert set(movement) == {
            "id",
            "event_type",
            "status",
            "impact_delta",
            "credit_delta",
            "institution",
            "report_id",
            "redemption_id",
            "created_at",
            "credited_at",
            "voided_at",
            "reversed_at",
        }
        if movement["event_type"] == RewardEventType.REDEMPTION:
            assert movement["redemption_id"] == str(own.pk)
            assert movement["report_id"] is None
            assert set(movement["institution"]) == INSTITUTION_KEYS
        else:
            assert movement["report_id"] == str(report.pk)
            assert movement["redemption_id"] is None
            assert movement["institution"] is None

    operator = _client(operator_user)
    response = operator.get("/api/v1/operator/rewards/")
    assert response.status_code == 200
    payload = _private(response)
    assert set(payload) == {"institution", "reward"}
    assert set(payload["institution"]) == INSTITUTION_KEYS
    assert set(payload["reward"]) == OPERATOR_REWARD_KEYS
    response = operator.get("/api/v1/operator/rewards/redemptions/?page=1")
    assert response.status_code == 200
    payload = _private(response)
    assert set(payload) == PAGINATION_KEYS
    assert len(payload["results"]) == 2
    for entry in payload["results"]:
        assert set(entry) == {"id", "code", "period", "created_at", "reward", "citizen_display"}
        assert set(entry["reward"]) == {"id", "name"}
        assert not {"email", "user", "user_id"} & set(entry)
        assert entry["period"] == periods.format_period(period)
        if entry["id"] == str(own.pk):
            assert entry["citizen_display"] == "María G."
        else:
            assert entry["citizen_display"] == "Ciudadano"
