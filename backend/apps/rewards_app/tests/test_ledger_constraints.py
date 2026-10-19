"""Constraint de coherencia estado <-> marcas temporales del ledger (D2-08).

Verifica los cinco casos válidos del diseño aprobado y los casos incoherentes que
la base de datos debe rechazar, además de los dos índices únicos parciales (D2-09).
"""

from datetime import date

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.reports_app.models import ReportConfirmation
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction

pytestmark = pytest.mark.django_db

AHORA = None


def _tx(citizen, **kwargs):
    datos = {
        "user": citizen,
        "event_type": RewardEventType.REPORT_CREATED,
        "impact_delta": 20,
        "credit_delta": 20,
        "status": RewardTxStatus.PENDIENTE,
    }
    datos.update(kwargs)
    return RewardTransaction.objects.create(**datos)


# --------------------------------------------------------------------------- #
# Casos válidos
# --------------------------------------------------------------------------- #


def test_pendiente_sin_ninguna_marca_temporal(citizen):
    tx = _tx(citizen)

    assert (tx.credited_at, tx.voided_at, tx.reversed_at) == (None, None, None)


def test_acreditado_con_credited_at(citizen, institution):
    tx = _tx(
        citizen,
        status=RewardTxStatus.ACREDITADO,
        institution=institution,
        credited_at=timezone.now(),
    )

    assert tx.credited_at is not None


def test_anulado_con_voided_at(citizen):
    tx = _tx(citizen, status=RewardTxStatus.ANULADO, voided_at=timezone.now())

    assert tx.voided_at is not None
    assert tx.credited_at is None


def test_revertido_original_conserva_su_credited_at(citizen, institution):
    """Caso A: la fila estuvo acreditada y la reversión no borra su rastro."""
    ahora = timezone.now()
    tx = _tx(
        citizen,
        status=RewardTxStatus.REVERTIDO,
        institution=institution,
        credited_at=ahora,
        reversed_at=ahora,
    )

    assert tx.credited_at is not None
    assert tx.reversed_at is not None
    assert tx.voided_at is None


def test_revertido_reversal_negativa_nace_sin_credited_at(citizen, institution, report):
    """Caso B: la fila REVERSAL nace REVERTIDO sin haber estado acreditada."""
    ahora = timezone.now()
    original = _tx(
        citizen,
        report=report,
        status=RewardTxStatus.REVERTIDO,
        institution=institution,
        credited_at=ahora,
        reversed_at=ahora,
        resolution_cycle=1,
    )
    tx = _tx(
        citizen,
        report=report,
        event_type=RewardEventType.REVERSAL,
        impact_delta=-20,
        credit_delta=-20,
        status=RewardTxStatus.REVERTIDO,
        institution=institution,
        reversed_at=ahora,
        resolution_cycle=1,
        related_transaction=original,
    )

    assert tx.credited_at is None
    assert tx.reversed_at is not None


# --------------------------------------------------------------------------- #
# Casos que la base de datos debe rechazar
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("estado", "marcas"),
    [
        (RewardTxStatus.PENDIENTE, {"credited_at": True}),
        (RewardTxStatus.PENDIENTE, {"voided_at": True}),
        (RewardTxStatus.PENDIENTE, {"reversed_at": True}),
        (RewardTxStatus.ACREDITADO, {}),
        (RewardTxStatus.ACREDITADO, {"credited_at": True, "voided_at": True}),
        (RewardTxStatus.ACREDITADO, {"credited_at": True, "reversed_at": True}),
        (RewardTxStatus.ANULADO, {}),
        (RewardTxStatus.ANULADO, {"voided_at": True, "credited_at": True}),
        (RewardTxStatus.ANULADO, {"voided_at": True, "reversed_at": True}),
        (RewardTxStatus.REVERTIDO, {}),
        (RewardTxStatus.REVERTIDO, {"reversed_at": True, "voided_at": True}),
    ],
)
def test_combinaciones_incoherentes_son_rechazadas(citizen, estado, marcas):
    ahora = timezone.now()
    campos = {clave: ahora for clave, activo in marcas.items() if activo}

    with pytest.raises(IntegrityError), transaction.atomic():
        _tx(citizen, status=estado, **campos)


# --------------------------------------------------------------------------- #
# Índices únicos parciales
# --------------------------------------------------------------------------- #


def test_un_solo_report_created_por_reporte(citizen, report):
    _tx(citizen, report=report)

    with pytest.raises(IntegrityError), transaction.atomic():
        _tx(citizen, report=report)


def test_el_mismo_reporte_admite_otros_tipos_de_evento(citizen, report, institution):
    ahora = timezone.now()
    _tx(citizen, report=report)
    _tx(
        citizen,
        report=report,
        event_type=RewardEventType.VALIDATION_BONUS,
        impact_delta=30,
        credit_delta=30,
        status=RewardTxStatus.ACREDITADO,
        institution=institution,
        credited_at=ahora,
        resolution_cycle=1,
    )
    _tx(
        citizen,
        report=report,
        event_type=RewardEventType.RESOLUTION_BONUS,
        impact_delta=50,
        credit_delta=50,
        status=RewardTxStatus.ACREDITADO,
        institution=institution,
        credited_at=ahora,
        resolution_cycle=1,
    )

    assert RewardTransaction.objects.filter(report=report).count() == 3


def test_dos_validation_bonus_son_posibles_tras_una_reversion(citizen, report, institution):
    """Cada ciclo admite su propio VALIDATION_BONUS."""
    ahora = timezone.now()
    for cycle in (1, 2):
        _tx(
            citizen,
            report=report,
            event_type=RewardEventType.VALIDATION_BONUS,
            impact_delta=30,
            credit_delta=30,
            status=RewardTxStatus.ACREDITADO,
            institution=institution,
            credited_at=ahora,
            resolution_cycle=cycle,
        )

    assert RewardTransaction.objects.filter(event_type="VALIDATION_BONUS").count() == 2


@pytest.mark.parametrize(
    "event_type",
    [RewardEventType.VALIDATION_BONUS, RewardEventType.RESOLUTION_BONUS],
)
def test_bonus_es_unico_por_reporte_y_ciclo(citizen, report, institution, event_type):
    ahora = timezone.now()
    delta = 30 if event_type == RewardEventType.VALIDATION_BONUS else 50
    values = {
        "report": report,
        "event_type": event_type,
        "impact_delta": delta,
        "credit_delta": delta,
        "status": RewardTxStatus.ACREDITADO,
        "institution": institution,
        "credited_at": ahora,
        "resolution_cycle": 1,
    }
    _tx(citizen, **values)

    with pytest.raises(IntegrityError), transaction.atomic():
        _tx(citizen, **values)


@pytest.mark.parametrize(
    "event_type",
    [RewardEventType.VALIDATION_BONUS, RewardEventType.RESOLUTION_BONUS],
)
def test_bonus_exige_report_y_ciclo_positivo(citizen, report, institution, event_type):
    ahora = timezone.now()
    delta = 30 if event_type == RewardEventType.VALIDATION_BONUS else 50
    common = {
        "event_type": event_type,
        "impact_delta": delta,
        "credit_delta": delta,
        "status": RewardTxStatus.ACREDITADO,
        "institution": institution,
        "credited_at": ahora,
    }
    invalid_cases = (
        {"report": None, "resolution_cycle": 1},
        {"report": report, "resolution_cycle": None},
        {"report": report, "resolution_cycle": 0},
    )

    for invalid in invalid_cases:
        with pytest.raises(IntegrityError), transaction.atomic():
            _tx(citizen, **common, **invalid)


def test_reversal_exige_report_ciclo_positivo_y_original(citizen, report, institution):
    ahora = timezone.now()
    original = _tx(
        citizen,
        report=report,
        status=RewardTxStatus.REVERTIDO,
        institution=institution,
        credited_at=ahora,
        reversed_at=ahora,
        resolution_cycle=1,
    )
    common = {
        "event_type": RewardEventType.REVERSAL,
        "impact_delta": -20,
        "credit_delta": -20,
        "status": RewardTxStatus.REVERTIDO,
        "institution": institution,
        "reversed_at": ahora,
    }
    invalid_cases = (
        {"report": None, "resolution_cycle": 1, "related_transaction": original},
        {"report": report, "resolution_cycle": None, "related_transaction": original},
        {"report": report, "resolution_cycle": 0, "related_transaction": original},
        {"report": report, "resolution_cycle": 1, "related_transaction": None},
    )

    for invalid in invalid_cases:
        with pytest.raises(IntegrityError), transaction.atomic():
            _tx(citizen, **common, **invalid)


def test_evento_no_reversal_no_admite_related_transaction(citizen, report, institution):
    ahora = timezone.now()
    original = _tx(
        citizen,
        report=report,
        status=RewardTxStatus.REVERTIDO,
        institution=institution,
        credited_at=ahora,
        reversed_at=ahora,
        resolution_cycle=1,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        _tx(
            citizen,
            event_type=RewardEventType.VALIDATION_BONUS,
            report=report,
            impact_delta=30,
            credit_delta=30,
            status=RewardTxStatus.ACREDITADO,
            institution=institution,
            credited_at=ahora,
            resolution_cycle=2,
            related_transaction=original,
        )


def test_reversal_es_unica_por_transaccion_original(citizen, report, institution):
    ahora = timezone.now()
    original = _tx(
        citizen,
        report=report,
        status=RewardTxStatus.REVERTIDO,
        institution=institution,
        credited_at=ahora,
        reversed_at=ahora,
        resolution_cycle=1,
    )
    values = {
        "report": report,
        "event_type": RewardEventType.REVERSAL,
        "impact_delta": -20,
        "credit_delta": -20,
        "status": RewardTxStatus.REVERTIDO,
        "institution": institution,
        "reversed_at": ahora,
        "resolution_cycle": 1,
        "related_transaction": original,
    }
    _tx(citizen, **values)

    with pytest.raises(IntegrityError), transaction.atomic():
        _tx(citizen, **values)


def test_un_solo_confirmation_created_por_confirmacion(report, other_citizen):
    confirmacion = ReportConfirmation.objects.create(report=report, citizen=other_citizen)
    datos = {
        "user": other_citizen,
        "report": report,
        "confirmation": confirmacion,
        "event_type": RewardEventType.CONFIRMATION_CREATED,
        "impact_delta": 10,
        "credit_delta": 10,
        "status": RewardTxStatus.PENDIENTE,
    }
    RewardTransaction.objects.create(**datos)

    with pytest.raises(IntegrityError), transaction.atomic():
        RewardTransaction.objects.create(**datos)


def test_varias_transacciones_pueden_no_tener_reporte_ni_confirmacion(citizen, institution):
    reward = Reward.objects.create(institution=institution, name="Vale simulado")
    now = timezone.now()
    for month in (10, 11):
        redemption = RewardRedemption.objects.create(
            user=citizen,
            reward=reward,
            institution=institution,
            period_month=date(2026, month, 1),
            code=f"CA-0000-0000-0000-00{month}",
            credits_spent=1000,
        )
        _tx(
            citizen,
            institution=institution,
            redemption=redemption,
            event_type=RewardEventType.REDEMPTION,
            impact_delta=0,
            credit_delta=-1000,
            status=RewardTxStatus.ACREDITADO,
            credited_at=now,
        )

    assert RewardTransaction.objects.filter(report__isnull=True).count() == 2
