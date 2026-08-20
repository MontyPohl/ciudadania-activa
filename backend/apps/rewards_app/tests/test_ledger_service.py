"""El contrato de dominio existe, pero el Sprint 2 no implementa el ledger."""

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


@pytest.mark.parametrize(("nombre", "parametros"), FUNCIONES)
def test_las_firmas_del_contrato_estan_declaradas(nombre, parametros):
    funcion = getattr(ledger, nombre)

    assert list(inspect.signature(funcion).parameters) == parametros


@pytest.mark.parametrize(("nombre", "parametros"), FUNCIONES)
def test_ninguna_funcion_del_ledger_esta_implementada(nombre, parametros):
    funcion = getattr(ledger, nombre)

    with pytest.raises(NotImplementedError):
        funcion(*[None] * len(parametros))


@pytest.mark.django_db
def test_el_esqueleto_no_escribe_en_el_ledger():
    from apps.rewards_app.models import RewardTransaction

    with pytest.raises(NotImplementedError):
        ledger.on_report_created(None)

    assert RewardTransaction.objects.count() == 0
