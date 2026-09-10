"""Contrato del ledger de creación, validación, resolución y reversión."""

import inspect

import pytest

from apps.rewards_app.services import ledger

FUNCIONES = [
    ("on_report_created", ["report"]),
    ("on_confirmation_created", ["confirmation"]),
    ("on_report_validated", ["report", "institution", "actor", "operation_at"]),
    ("on_report_rejected", ["report", "operation_at"]),
    ("on_report_resolved", ["report", "actor"]),
    ("revert_validation", ["report", "actor", "reason"]),
]

@pytest.mark.parametrize(("nombre", "parametros"), FUNCIONES)
def test_las_firmas_del_contrato_estan_declaradas(nombre, parametros):
    funcion = getattr(ledger, nombre)

    assert list(inspect.signature(funcion).parameters) == parametros


@pytest.mark.django_db
def test_on_report_created_escribe_y_retorna_el_asiento_exacto(report, citizen):
    transaction = ledger.on_report_created(report)

    assert transaction.user == citizen
    assert transaction.report == report
    assert transaction.event_type == "REPORT_CREATED"
    assert transaction.impact_delta == 20
    assert transaction.credit_delta == 20
    assert transaction.status == "PENDIENTE"


@pytest.mark.django_db
def test_on_report_created_rechaza_explicita_y_sin_escritura_reporte_sin_ciudadano(
    category,
):
    from django.contrib.gis.geos import Point

    from apps.reports_app.models import Report
    from apps.rewards_app.models import RewardTransaction

    report = Report.objects.create(
        citizen=None,
        category=category,
        description="Reporte sin ciudadano.",
        location=Point(-57.63, -25.26, srid=4326),
    )

    with pytest.raises(ValueError, match="exige un reporte con ciudadano"):
        ledger.on_report_created(report)

    assert RewardTransaction.objects.count() == 0


@pytest.mark.django_db
def test_on_confirmation_created_escribe_y_retorna_el_asiento_exacto(report, other_citizen):
    from apps.reports_app.models import ReportConfirmation

    confirmation = ReportConfirmation.objects.create(
        report=report,
        citizen=other_citizen,
    )

    transaction = ledger.on_confirmation_created(confirmation)

    assert transaction.user == other_citizen
    assert transaction.report is None
    assert transaction.confirmation == confirmation
    assert transaction.event_type == "CONFIRMATION_CREATED"
    assert transaction.impact_delta == 10
    assert transaction.credit_delta == 10
    assert transaction.status == "PENDIENTE"


@pytest.mark.django_db
def test_on_confirmation_created_acredita_si_el_reporte_ya_fue_validado(
    report,
    other_citizen,
    institution,
):
    from django.utils import timezone

    from apps.reports_app.models import ReportConfirmation

    report.validated_at = timezone.now()
    report.institution = institution
    report.status = "ASIGNADO"
    report.resolution_cycle = 1
    report.save(
        update_fields=[
            "validated_at",
            "institution",
            "status",
            "resolution_cycle",
            "updated_at",
        ]
    )
    confirmation = ReportConfirmation.objects.create(
        report=report,
        citizen=other_citizen,
    )

    transaction = ledger.on_confirmation_created(confirmation)

    assert transaction.status == "ACREDITADO"
    assert transaction.institution == institution
    assert transaction.credited_at == confirmation.created_at
    assert transaction.resolution_cycle == 1


@pytest.mark.django_db
def test_resolution_bonus_es_50_50_y_pertenece_al_ciclo(report, operator_user, institution):
    from django.utils import timezone

    report.validated_at = timezone.now()
    report.institution = institution
    report.status = "ASIGNADO"
    report.resolution_cycle = 1
    report.save(
        update_fields=[
            "validated_at",
            "institution",
            "status",
            "resolution_cycle",
            "updated_at",
        ]
    )

    result = ledger.on_report_resolved(report, operator_user)

    bonus = result.resolution_bonus
    assert (bonus.impact_delta, bonus.credit_delta) == (50, 50)
    assert bonus.status == "ACREDITADO"
    assert bonus.resolution_cycle == 1


@pytest.mark.django_db
def test_reversal_conserva_deltas_y_crea_contrapartidas(
    report,
    operator_user,
    institution,
):
    from django.utils import timezone

    from apps.rewards_app.enums import RewardEventType, RewardTxStatus
    from apps.rewards_app.models import RewardTransaction

    now = timezone.now()
    report.validated_at = now
    report.institution = institution
    report.status = "ASIGNADO"
    report.resolution_cycle = 1
    report.save(
        update_fields=[
            "validated_at",
            "institution",
            "status",
            "resolution_cycle",
            "updated_at",
        ]
    )
    originals = [
        RewardTransaction.objects.create(
            user=report.citizen,
            report=report,
            institution=institution,
            event_type=event_type,
            impact_delta=delta,
            credit_delta=delta,
            status=RewardTxStatus.ACREDITADO,
            credited_at=now,
            resolution_cycle=1,
        )
        for event_type, delta in (
            (RewardEventType.REPORT_CREATED, 20),
            (RewardEventType.VALIDATION_BONUS, 30),
            (RewardEventType.RESOLUTION_BONUS, 50),
        )
    ]

    result = ledger.revert_validation(report, operator_user, "Motivo administrativo suficiente")

    for original in originals:
        original.refresh_from_db()
        assert original.status == RewardTxStatus.REVERTIDO
        assert original.credited_at == now
        assert original.impact_delta > 0 and original.credit_delta > 0
    assert [tx.related_transaction_id for tx in result.reversal_transactions] == [
        original.pk for original in originals
    ]
    assert [tx.impact_delta for tx in result.reversal_transactions] == [-20, -30, -50]
    assert all(tx.status == RewardTxStatus.REVERTIDO for tx in result.reversal_transactions)


@pytest.mark.django_db
def test_on_confirmation_created_falla_cerrado_ante_validacion_sin_institucion(
    report,
    other_citizen,
):
    from django.utils import timezone

    from apps.reports_app.models import ReportConfirmation
    from apps.rewards_app.models import RewardTransaction

    report.validated_at = timezone.now()
    report.save(update_fields=["validated_at", "updated_at"])
    confirmation = ReportConfirmation.objects.create(
        report=report,
        citizen=other_citizen,
    )

    with pytest.raises(ledger.LedgerInvariantError):
        ledger.on_confirmation_created(confirmation)

    assert RewardTransaction.objects.count() == 0
