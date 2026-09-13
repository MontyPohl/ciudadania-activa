import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction

pytestmark = pytest.mark.django_db


def test_los_cuatro_estados_del_ledger_existen():
    assert RewardTxStatus.values == ["PENDIENTE", "ACREDITADO", "ANULADO", "REVERTIDO"]


def test_anulado_esta_disponible_como_cuarto_estado():
    """C-01 / CT-06: un reporte rechazado no deja transacciones pendientes."""
    assert "ANULADO" in RewardTxStatus.values
    assert RewardTxStatus.ANULADO != RewardTxStatus.REVERTIDO


def test_los_seis_tipos_de_evento_economico():
    assert RewardEventType.values == [
        "REPORT_CREATED",
        "CONFIRMATION_CREATED",
        "VALIDATION_BONUS",
        "RESOLUTION_BONUS",
        "REDEMPTION",
        "REVERSAL",
    ]


def test_transaccion_pendiente_de_creacion_de_reporte(citizen, report):
    tx = RewardTransaction.objects.create(
        user=citizen,
        report=report,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=20,
        credit_delta=20,
        status=RewardTxStatus.PENDIENTE,
    )

    assert tx.institution is None
    assert tx.credited_at is None
    assert str(tx) == "REPORT_CREATED +20/+20 PENDIENTE"


def test_no_se_puede_borrar_al_sujeto_de_una_transaccion(citizen):
    RewardTransaction.objects.create(
        user=citizen,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=20,
        credit_delta=20,
        status=RewardTxStatus.PENDIENTE,
    )

    with pytest.raises(ProtectedError):
        citizen.delete()


def test_no_se_puede_borrar_un_reporte_con_historia_economica(citizen, report):
    RewardTransaction.objects.create(
        user=citizen,
        report=report,
        event_type=RewardEventType.REPORT_CREATED,
        impact_delta=20,
        credit_delta=20,
        status=RewardTxStatus.PENDIENTE,
    )

    with pytest.raises(ProtectedError):
        report.delete()


def test_la_fila_reversal_apunta_a_su_original(citizen, institution, report):
    ahora = timezone.now()
    original = RewardTransaction.objects.create(
        user=citizen,
        report=report,
        institution=institution,
        event_type=RewardEventType.VALIDATION_BONUS,
        impact_delta=30,
        credit_delta=30,
        status=RewardTxStatus.REVERTIDO,
        credited_at=ahora,
        reversed_at=ahora,
        resolution_cycle=1,
    )
    reversal = RewardTransaction.objects.create(
        user=citizen,
        report=report,
        institution=institution,
        event_type=RewardEventType.REVERSAL,
        impact_delta=-30,
        credit_delta=-30,
        status=RewardTxStatus.REVERTIDO,
        reversed_at=ahora,
        related_transaction=original,
        resolution_cycle=1,
    )

    assert reversal.related_transaction == original
    assert original.reversals.get() == reversal

    with pytest.raises(ProtectedError):
        original.delete()


def test_recompensa_institucional_con_costo_fijo(institution):
    reward = Reward.objects.create(institution=institution, name="Descuento simulado")

    assert reward.credit_cost == 1000
    assert reward.active is True
    assert reward.simulated is True
    assert str(reward) == f"Descuento simulado ({institution.pk})"


def test_una_recompensa_por_nombre_e_institucion(institution):
    Reward.objects.create(institution=institution, name="Descuento simulado")

    with pytest.raises(IntegrityError), transaction.atomic():
        Reward.objects.create(institution=institution, name="Descuento simulado")


def test_el_costo_de_la_recompensa_debe_ser_positivo(institution):
    with pytest.raises(IntegrityError), transaction.atomic():
        Reward.objects.create(institution=institution, name="Gratis", credit_cost=0)


def test_canje_con_codigo_unico(citizen, institution):
    reward = Reward.objects.create(institution=institution, name="Descuento simulado")
    canje = RewardRedemption.objects.create(user=citizen, reward=reward, code="VALE-0001")

    assert canje.credits_spent == 1000
    assert str(canje) == "VALE-0001"

    with pytest.raises(IntegrityError), transaction.atomic():
        RewardRedemption.objects.create(user=citizen, reward=reward, code="VALE-0001")


def test_no_se_puede_borrar_una_recompensa_canjeada(citizen, institution):
    reward = Reward.objects.create(institution=institution, name="Descuento simulado")
    RewardRedemption.objects.create(user=citizen, reward=reward, code="VALE-0002")

    with pytest.raises(ProtectedError):
        reward.delete()
