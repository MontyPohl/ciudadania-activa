"""Aviso de recompensa disponible (Sprint 13, S13-D10).

A lo sumo uno por ciudadano, institución y mes; nunca a quien ya canjeó ese mes, ni por
una recompensa inactiva, ni por debajo del costo fijo.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from apps.admin_app.models import Institution
from apps.auth_app.enums import VerificationStatus
from apps.notifications_app.enums import NotificationType
from apps.notifications_app.models import Notification
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction
from apps.rewards_app.services.availability import notify_available_rewards

pytestmark = pytest.mark.django_db

LOCAL = ZoneInfo("America/Asuncion")
HOY = datetime(2026, 10, 20, 9, 0, tzinfo=LOCAL)
MES_SIGUIENTE = datetime(2026, 11, 1, 9, 0, tzinfo=LOCAL)


def _verificado(user_factory, email):
    return user_factory(email=email, verification_status=VerificationStatus.APROBADO)


def _acreditar(user, institution, amount):
    """Fila ACREDITADO sintética, con el mismo helper que `test_redemptions.py` (S12)."""
    RewardTransaction.objects.create(
        user=user,
        institution=institution,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=amount,
        credit_delta=amount,
        status=RewardTxStatus.ACREDITADO,
        credited_at=HOY,
    )


def test_recompensa_disponible_una_vez_por_mes_e_institucion(user_factory, institution):
    otra = Institution.objects.exclude(pk=institution.pk).first()
    recompensa = Reward.objects.create(institution=institution, name="Vale simulado A")
    Reward.objects.create(institution=otra, name="Vale simulado B", active=False)

    elegible = _verificado(user_factory, "elegible@example.com")
    _acreditar(elegible, institution, 1000)
    _acreditar(elegible, otra, 1500)  # su recompensa está inactiva

    corto = _verificado(user_factory, "corto@example.com")
    _acreditar(corto, institution, 999)

    # Ya canjeó este mes y aun así le queda saldo: lo excluye el canje, no el saldo.
    canjeo = _verificado(user_factory, "canjeo@example.com")
    _acreditar(canjeo, institution, 2200)
    vale = RewardRedemption.objects.create(
        user=canjeo,
        reward=recompensa,
        institution=institution,
        period_month=date(2026, 10, 1),
        code="CA-0000-0000-0000-0001",
    )
    RewardTransaction.objects.create(
        user=canjeo,
        institution=institution,
        redemption=vale,
        event_type=RewardEventType.REDEMPTION,
        impact_delta=0,
        credit_delta=-1000,
        status=RewardTxStatus.ACREDITADO,
        credited_at=HOY,
    )

    assert notify_available_rewards(now=HOY) == 1
    aviso = Notification.objects.get(notification_type=NotificationType.RECOMPENSA_DISPONIBLE)
    assert aviso.user == elegible
    assert aviso.institution == institution
    assert aviso.dedup_key == (f"RECOMPENSA_DISPONIBLE:{elegible.pk}:{institution.pk}:2026-10")

    # Mismo mes: nada nuevo.
    assert notify_available_rewards(now=HOY) == 0

    # Mes siguiente: el ciudadano que canjeó en octubre vuelve a ser elegible, y el
    # primero recibe su aviso del nuevo mes.
    assert notify_available_rewards(now=MES_SIGUIENTE) == 2
    assert (
        Notification.objects.filter(
            notification_type=NotificationType.RECOMPENSA_DISPONIBLE
        ).count()
        == 3
    )
