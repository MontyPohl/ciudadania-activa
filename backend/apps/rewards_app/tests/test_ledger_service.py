"""Contrato del ledger: creación implementada en S6 y demás eventos aún declarativos."""

import inspect

import pytest

from apps.rewards_app.services import ledger

FUNCIONES = [
    ("on_report_created", ["report"]),
    ("on_confirmation_created", ["confirmation"]),
    ("on_report_validated", ["report", "institution", "actor"]),
    ("on_report_resolved", ["report", "actor"]),
    ("revert_validation", ["report", "actor", "reason"]),
]

FUNCIONES_PENDIENTES = FUNCIONES[2:]


@pytest.mark.parametrize(("nombre", "parametros"), FUNCIONES)
def test_las_firmas_del_contrato_estan_declaradas(nombre, parametros):
    funcion = getattr(ledger, nombre)

    assert list(inspect.signature(funcion).parameters) == parametros


@pytest.mark.parametrize(("nombre", "parametros"), FUNCIONES_PENDIENTES)
def test_ninguna_funcion_del_ledger_esta_implementada(nombre, parametros):
    funcion = getattr(ledger, nombre)

    with pytest.raises(NotImplementedError):
        funcion(*[None] * len(parametros))


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
