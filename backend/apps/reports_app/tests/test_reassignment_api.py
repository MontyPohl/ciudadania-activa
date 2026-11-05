"""Reasignación de un reporte sin validar a otra institución. Posterior a S11."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.admin_app.models import AuditLog, Category, Institution, InstitutionOperator
from apps.ai_client_app import tasks as ai_tasks
from apps.ai_client_app.adapters import FakeAiClassificationAdapter
from apps.ai_client_app.adapters.runtime import RuntimeAdapter
from apps.ai_client_app.models import AiClassification
from apps.ai_client_app.orchestration import AUDIT_ACTION
from apps.ai_client_app.tasks import classify_report
from apps.auth_app.enums import UserRole
from apps.core.enums import ReportStatus
from apps.notifications_app.models import Notification
from apps.reports_app.models import ReportReassignment, ReportStatusHistory
from apps.reports_app.services import reversal, validation
from apps.rewards_app.enums import RewardTxStatus
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db


@pytest.fixture
def ande(db):
    return Institution.objects.get(slug="ande")


@pytest.fixture
def alumbrado(db):
    return Category.objects.get(slug="alumbrado-publico")


@pytest.fixture
def operator_ande(user_factory, ande):
    user = user_factory(email="ande@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=user, institution=ande, active=True)
    return user


def _post(user, report, institution, **body):
    client = APIClient()
    client.force_authenticate(user)
    return client.post(
        f"/api/v1/operator/reports/{report.pk}/reassign/?institution_id={institution.pk}",
        body,
        format="json",
    )


def _context(user, report, institution):
    client = APIClient()
    client.force_authenticate(user)
    return client.get(
        f"/api/v1/operator/reports/{report.pk}/reassignment-context/"
        f"?institution_id={institution.pk}"
    )


def test_reasignar_mueve_categoria_e_institucion_sin_tocar_lo_demas(
    operator_user, operator_ande, institution, ande, alumbrado, report
):
    reward = ledger.on_report_created(report)
    historial_previo = ReportStatusHistory.objects.count()
    avisos_previos = Notification.objects.count()

    response = _post(
        operator_user, report, institution, category_id=str(alumbrado.pk), reason="  Es de ANDE. "
    )

    assert response.status_code == 200
    report.refresh_from_db()
    assert report.category == alumbrado
    assert report.institution == ande
    assert report.status == ReportStatus.CREADO
    reward.refresh_from_db()
    assert reward.status == RewardTxStatus.PENDIENTE
    assert RewardTransaction.objects.filter(report=report).count() == 1
    assert ReportStatusHistory.objects.count() == historial_previo
    assert Notification.objects.count() == avisos_previos
    assert ReportReassignment.objects.get(report=report).reason == "Es de ANDE."
    assert AuditLog.objects.filter(action="report.reassigned").count() == 1

    # Deja de ser visible para quien lo pasó, y quien lo recibe ve el motivo.
    assert _context(operator_user, report, institution).status_code == 404
    contexto = _context(operator_ande, report, ande).json()
    assert contexto["history"][0]["reason"] == "Es de ANDE."
    assert contexto["history"][0]["to_institution"]["slug"] == "ande"


def test_los_destinos_excluyen_a_la_propia_institucion(operator_user, institution, report):
    opciones = _context(operator_user, report, institution).json()["options"]

    assert opciones
    assert all(opcion["institution"]["id"] != str(institution.pk) for opcion in opciones)
    assert {"category", "institution"} == set(opciones[0])


@pytest.mark.parametrize("motivo", ["", "   ", "x" * 501])
def test_el_motivo_es_obligatorio_y_corto(operator_user, institution, alumbrado, report, motivo):
    response = _post(
        operator_user, report, institution, category_id=str(alumbrado.pk), reason=motivo
    )

    assert response.status_code == 400
    assert not ReportReassignment.objects.exists()


@pytest.mark.parametrize("destino", ["propia", "inactiva"])
def test_rechaza_destinos_no_validos(
    operator_user, institution, category, alumbrado, report, destino
):
    objetivo = category if destino == "propia" else alumbrado
    if destino == "inactiva":
        Category.objects.filter(pk=alumbrado.pk).update(is_active=False)

    response = _post(operator_user, report, institution, category_id=str(objetivo.pk), reason="x")

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REASSIGNMENT_TARGET"
    assert not ReportReassignment.objects.exists()


def test_un_reporte_validado_no_se_reasigna(operator_user, institution, alumbrado, report):
    ledger.on_report_created(report)
    client = APIClient()
    client.force_authenticate(operator_user)
    client.post(f"/api/v1/operator/reports/{report.pk}/validate/?institution_id={institution.pk}")

    response = _post(operator_user, report, institution, category_id=str(alumbrado.pk), reason="x")

    assert response.status_code == 409
    assert response.json()["code"] == "VALIDATION_REVERSAL_REQUIRED"


def test_otra_institucion_no_puede_reasignar(operator_ande, ande, category, report):
    response = _post(operator_ande, report, ande, category_id=str(category.pk), reason="x")

    assert response.status_code == 404
    assert not ReportReassignment.objects.exists()


def test_la_ia_no_pisa_una_reasignacion_previa(
    monkeypatch, operator_user, institution, alumbrado, ande, report
):
    _post(operator_user, report, institution, category_id=str(alumbrado.pk), reason="Es de ANDE.")
    monkeypatch.setattr(
        ai_tasks,
        "get_runtime_adapter",
        lambda: RuntimeAdapter(adapter=FakeAiClassificationAdapter(), model_name="fake"),
    )

    classify_report(str(report.pk))

    report.refresh_from_db()
    assert AiClassification.objects.filter(report=report).exists()
    assert report.status == ReportStatus.CLASIFICADO
    assert report.category == alumbrado
    assert report.institution == ande
    # El operador no debe ver esa sugerencia como «aplicada sin revisión».
    auditoria = AuditLog.objects.get(action=AUDIT_ACTION, payload__report_id=str(report.pk))
    assert auditoria.payload["applied"] is False
    assert auditoria.payload["requires_human_review"] is True


def test_una_validacion_revertida_no_se_reasigna(
    operator_user, admin_user, institution, alumbrado, report
):
    ledger.on_report_created(report)
    validation.validate_report(report_id=report.pk, operator=operator_user, institution=institution)
    reversal.revert_report_validation(
        report_id=report.pk, admin_id=admin_user.pk, expected_cycle=1, reason="Corrección."
    )

    response = _post(operator_user, report, institution, category_id=str(alumbrado.pk), reason="x")

    assert response.status_code == 409
    assert response.json()["code"] == "REPORT_STATE_NOT_ALLOWED"
    assert not ReportReassignment.objects.exists()


def test_los_traspasos_bloquean_las_instituciones_en_orden(
    operator_user, institution, alumbrado, report
):
    with CaptureQueriesContext(connection) as queries:
        _post(operator_user, report, institution, category_id=str(alumbrado.pk), reason="x")

    bloqueo = next(
        q["sql"] for q in queries if 'FROM "institutions"' in q["sql"] and "FOR UPDATE" in q["sql"]
    )
    assert 'ORDER BY "institutions"."id" ASC' in bloqueo
