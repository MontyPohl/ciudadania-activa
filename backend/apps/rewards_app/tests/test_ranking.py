from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.auth_app.enums import RankingVisibility, UserRole
from apps.auth_app.models import CitizenProfile
from apps.rewards_app import periods, ranking
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 19, 15, tzinfo=UTC)
URL = "/api/v1/ranking/monthly/"


def _transaction(user, points, *, at=NOW, status=RewardTxStatus.ACREDITADO, **extra):
    return RewardTransaction.objects.create(
        user=user,
        impact_delta=points,
        credit_delta=points,
        status=status,
        credited_at=at if status in (RewardTxStatus.ACREDITADO, RewardTxStatus.REVERTIDO) else None,
        voided_at=at if status == RewardTxStatus.ANULADO else None,
        reversed_at=at if status == RewardTxStatus.REVERTIDO else None,
        event_type=extra.pop("event_type", RewardEventType.REPORT_CREATED),
        **extra,
    )


def test_visibilidad_y_privacidad(user_factory, monkeypatch):
    cache.delete("ranking:v1:2026-10")
    monkeypatch.setattr("apps.rewards_app.ranking_views.timezone.now", lambda: NOW)
    modes = [
        (RankingVisibility.FULL_NAME, " Ana López ", "", "Ana López", False),
        (RankingVisibility.FIRST_INITIAL, "María  González", "", "María G.", False),
        (RankingVisibility.ALIAS, "Nombre reservado", "Vecina activa", "Vecina activa", False),
        (RankingVisibility.PRIVATE, "Nombre privado", "", "Usuario privado", True),
        (RankingVisibility.ALIAS, "Alias vacío", "", "Usuario privado", True),
        (None, "Sin perfil", "", "Usuario privado", True),
    ]
    users = []
    for index, (mode, name, alias, _, _) in enumerate(modes):
        user = user_factory(email=f"ranking{index}@example.com", full_name=name)
        users.append(user)
        if mode is not None:
            CitizenProfile.objects.create(
                user=user,
                document_hmac=f"{index:064x}",
                ranking_visibility=mode,
                ranking_alias=alias,
            )
        _transaction(user, 180 - index * 10)

    client = APIClient()
    response = client.get(URL)
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert set(response.data) == {"period", "entries"}
    assert response.data["period"] == "2026-10"
    entries = response.data["entries"]
    assert len(entries) == len(modes)
    for index, entry in enumerate(entries):
        assert entry == {
            "position": index + 1,
            "display_name": modes[index][3],
            "points": 180 - index * 10,
            "is_private": modes[index][4],
            "is_me": False,
        }
        assert not {"email", "id", "user_id"} & entry.keys()

    stored = cache.get("ranking:v1:2026-10")
    assert stored == [ranking.RankingRow(user.pk, 180 - i * 10) for i, user in enumerate(users)]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(users[3])}")
    response = client.get(URL)
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert [entry["is_me"] for entry in response.data["entries"]] == [
        False,
        False,
        False,
        True,
        False,
        False,
    ]

    # La privacidad cambia en la próxima petición aunque los puntajes sigan cacheados.
    CitizenProfile.objects.filter(user=users[0]).update(
        ranking_visibility=RankingVisibility.PRIVATE
    )
    response = client.get(URL)
    assert response.data["entries"][0] == {
        **entries[0],
        "display_name": "Usuario privado",
        "is_private": True,
    }
    assert cache.get("ranking:v1:2026-10") == stored
    response = client.get(URL, {"period": "2026-09"})
    assert response.status_code == 400
    assert response.data == {"detail": {"period": ["Este campo no está permitido."]}}
    assert response["Cache-Control"] == "private, no-store"


def test_corte_mensual_y_estados(user_factory, monkeypatch):
    cache.delete("ranking:v1:2026-10")
    monkeypatch.setattr("apps.rewards_app.ranking_views.timezone.now", lambda: NOW)
    start, end = periods.month_bounds(periods.current_period(NOW))
    assert start.astimezone(UTC) == datetime(2026, 10, 1, 3, tzinfo=UTC)
    users = [user_factory(email=f"corte{i}@example.com", id=UUID(int=i + 1)) for i in range(12)]
    # La suma prima sobre una fila individual; el MAX determina cuándo se llegó al total.
    _transaction(users[0], 90, at=start)
    _transaction(users[0], 10, at=NOW)
    _transaction(users[1], 100, at=start + timedelta(hours=1))
    _transaction(users[2], 100, at=NOW)
    for index, user in enumerate(users[3:]):
        _transaction(user, 90 - index, at=end - timedelta(microseconds=1))
    _transaction(users[0], 500, at=start - timedelta(microseconds=1))
    _transaction(users[0], 500, at=end)
    for status in (RewardTxStatus.PENDIENTE, RewardTxStatus.ANULADO, RewardTxStatus.REVERTIDO):
        _transaction(users[0], 500, status=status)
    _transaction(users[0], 500, event_type=RewardEventType.REDEMPTION)

    for index, extra in enumerate(
        [{"role": UserRole.OPERATOR}, {"role": UserRole.ADMIN}, {"is_active": False}, {}, {}]
    ):
        user = user_factory(email=f"excluido{index}@example.com", **extra)
        _transaction(user, 500 if index < 3 else -index + 3)

    expected = [users[1], users[0], users[2], *users[3:10]]
    rows = ranking.monthly_top(now=NOW)
    assert [row.user_id for row in rows] == [user.pk for user in expected]
    assert [row.points for row in rows] == [100, 100, 100, 90, 89, 88, 87, 86, 85, 84]
    response = APIClient().get(URL)
    assert response.status_code == 200
    assert response.data["period"] == "2026-10"
    assert len(response.data["entries"]) == 10
    assert [entry["position"] for entry in response.data["entries"]] == list(range(1, 11))
    assert [entry["points"] for entry in response.data["entries"]] == [row.points for row in rows]
