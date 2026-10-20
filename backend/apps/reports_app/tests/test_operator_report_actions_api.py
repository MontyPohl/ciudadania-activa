"""Mutaciones institucionales HTTP de Sprint 9-B2."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.db import connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.admin_app.models import AuditLog, Category, Institution, InstitutionOperator
from apps.ai_client_app.models import AiClassification
from apps.auth_app.enums import UserRole
from apps.core.enums import RejectionReason, ReportStatus
from apps.reports_app.models import Report, ReportStatusHistory
from apps.reports_app.services import validation
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def _report(category, **overrides):
    values = {
        "category": category,
        "description": "Incidencia para decisión institucional.",
        "location": Point(-57.6359, -25.2637, srid=4326),
    }
    values.update(overrides)
    return Report.objects.create(**values)


def _url(report, action, institution):
    report_id = getattr(report, "pk", report)
    institution_id = getattr(institution, "pk", institution)
    return f"/api/v1/operator/reports/{report_id}/{action}/" f"?institution_id={institution_id}"


def _creation_reward(report):
    return ledger.on_report_created(report)


def test_validate_http_persiste_estado_y_ledger(
    operator_user,
    institution,
    report,
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    created = _creation_reward(report)
    invalidations = []
    monkeypatch.setattr(
        validation,
        "invalidate_public_reports_cache",
        lambda: invalidations.append("called"),
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        response = _client(operator_user).post(
            _url(report, "validate", institution), {}, format="json"
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(report.pk),
        "status": ReportStatus.ASIGNADO,
        "institution": {
            "id": str(institution.pk),
            "name": institution.name,
            "slug": institution.slug,
        },
        "validated_at": response.json()["validated_at"],
        "validated_by": str(operator_user.pk),
        "rejection_reason": "",
    }
    assert response.json()["validated_at"] is not None
    report.refresh_from_db()
    created.refresh_from_db()
    bonus = RewardTransaction.objects.get(event_type=RewardEventType.VALIDATION_BONUS)
    assert report.status == ReportStatus.ASIGNADO
    assert report.institution == institution
    assert report.validated_by == operator_user
    assert created.status == RewardTxStatus.ACREDITADO
    assert created.institution == institution
    assert (bonus.impact_delta, bonus.credit_delta) == (30, 30)
    assert invalidations == []
    # Invalidación del mapa y, registrado después (S13-D13), el aviso de validación.
    assert len(callbacks) == 2
    callbacks[0]()
    assert invalidations == ["called"]


def test_reject_http_persiste_estado_y_anula_pendientes(
    operator_user,
    institution,
    report,
    django_capture_on_commit_callbacks,
):
    created = _creation_reward(report)

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(operator_user).post(
            _url(report, "reject", institution),
            {"reason": RejectionReason.DUPLICADO},
            format="json",
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(report.pk),
        "status": ReportStatus.RECHAZADO,
        "institution": None,
        "validated_at": None,
        "validated_by": None,
        "rejection_reason": RejectionReason.DUPLICADO,
    }
    report.refresh_from_db()
    created.refresh_from_db()
    assert report.status == ReportStatus.RECHAZADO
    assert report.rejection_reason == RejectionReason.DUPLICADO
    assert created.status == RewardTxStatus.ANULADO
    assert not RewardTransaction.objects.filter(status=RewardTxStatus.ACREDITADO).exists()


@pytest.mark.parametrize("action", ["validate", "reject"])
def test_institution_id_es_obligatorio(operator_user, report, action):
    payload = {} if action == "validate" else {"reason": RejectionReason.DUPLICADO}

    response = _client(operator_user).post(
        f"/api/v1/operator/reports/{report.pk}/{action}/",
        payload,
        format="json",
    )

    assert response.status_code == 400
    assert "institution_id" in response.json()["detail"]


@pytest.mark.parametrize("value", ["no-es-uuid", ""])
def test_institution_id_invalido_es_400(operator_user, report, value):
    response = _client(operator_user).post(
        f"/api/v1/operator/reports/{report.pk}/validate/?institution_id={value}",
        {},
        format="json",
    )

    assert response.status_code == 400
    assert "institution_id" in response.json()["detail"]


def test_institution_id_repetido_es_400(operator_user, institution, report):
    response = _client(operator_user).post(
        f"/api/v1/operator/reports/{report.pk}/validate/"
        f"?institution_id={institution.pk}&institution_id={institution.pk}",
        {},
        format="json",
    )

    assert response.status_code == 400
    assert "institution_id" in response.json()["detail"]


def test_validate_exige_body_vacio(operator_user, institution, report):
    response = _client(operator_user).post(
        _url(report, "validate", institution),
        {"reason": RejectionReason.DUPLICADO},
        format="json",
    )

    assert response.status_code == 400
    assert "reason" in response.json()["detail"]
    assert ReportStatusHistory.objects.count() == 0


@pytest.mark.parametrize("payload", [{}, {"otro": "dato"}])
def test_reject_exige_reason(operator_user, institution, report, payload):
    response = _client(operator_user).post(
        _url(report, "reject", institution), payload, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REJECTION_REASON"
    assert ReportStatusHistory.objects.count() == 0


def test_reject_rechaza_reason_fuera_de_dominio(operator_user, institution, report):
    response = _client(operator_user).post(
        _url(report, "reject", institution), {"reason": "inventado"}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REJECTION_REASON"
    assert ReportStatusHistory.objects.count() == 0


def test_reject_rechaza_campos_adicionales(operator_user, institution, report):
    response = _client(operator_user).post(
        _url(report, "reject", institution),
        {"reason": RejectionReason.DUPLICADO, "institution_id": str(institution.pk)},
        format="json",
    )

    assert response.status_code == 400
    assert "institution_id" in response.json()["detail"]


@pytest.mark.parametrize("action", ["validate", "reject"])
@pytest.mark.parametrize("payload", [[], "x", 1, True, None])
def test_body_json_no_objeto_es_400_sin_efectos(
    operator_user,
    institution,
    report,
    action,
    payload,
    monkeypatch,
):
    invalidations = []
    monkeypatch.setattr(
        validation,
        "invalidate_public_reports_cache",
        lambda: invalidations.append("called"),
    )
    before = Report.objects.values(
        "status",
        "institution_id",
        "validated_at",
        "validated_by_id",
        "rejection_reason",
        "updated_at",
    ).get(pk=report.pk)

    response = _client(operator_user).post(
        _url(report, action, institution),
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert Report.objects.values(*before).get(pk=report.pk) == before
    assert ReportStatusHistory.objects.count() == 0
    assert RewardTransaction.objects.count() == 0
    assert AuditLog.objects.count() == 0
    assert invalidations == []


@pytest.mark.parametrize("action", ["validate", "reject"])
def test_reporte_inexistente_y_fuera_de_scope_comparten_404(
    operator_user,
    institution,
    category,
    action,
):
    other = Institution.objects.create(name="Otra institución", slug="otra-institucion-s9-b2")
    outside = _report(category, institution=other)
    payload = {} if action == "validate" else {"reason": RejectionReason.DUPLICADO}
    client = _client(operator_user)

    hidden = client.post(_url(outside, action, institution), payload, format="json")
    missing = client.post(_url(uuid.uuid4(), action, institution), payload, format="json")

    assert hidden.status_code == missing.status_code == 404
    assert (
        hidden.json()
        == missing.json()
        == {
            "detail": "No se encontró el reporte solicitado.",
            "code": "REPORT_NOT_FOUND",
        }
    )


@pytest.mark.parametrize("action", ["validate", "reject"])
def test_cross_tenant_no_puede_operar(
    user_factory,
    institution,
    category,
    action,
):
    other = Institution.objects.create(name="ANDE B2", slug="ande-b2")
    operator = user_factory(email="ande-b2@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=operator, institution=other, active=True)
    report = _report(category, institution=institution)
    payload = {} if action == "validate" else {"reason": RejectionReason.DUPLICADO}

    response = _client(operator).post(_url(report, action, other), payload, format="json")

    assert response.status_code == 404
    assert response.json()["code"] == "REPORT_NOT_FOUND"
    assert ReportStatusHistory.objects.count() == 0


@pytest.mark.parametrize("action", ["validate", "reject"])
def test_operador_sin_membresia_activa_recibe_codigo_estable(
    user_factory,
    institution,
    report,
    action,
):
    operator = user_factory(email=f"sin-vinculo-{action}@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=operator, institution=institution, active=False)
    payload = {} if action == "validate" else {"reason": RejectionReason.DUPLICADO}

    response = _client(operator).post(_url(report, action, institution), payload, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"


def test_operador_sin_ninguna_membresia_recibe_codigo_estable(
    user_factory,
    institution,
    report,
):
    operator = user_factory(email="sin-membresia-b2@example.com", role=UserRole.OPERATOR)

    response = _client(operator).post(_url(report, "validate", institution), {}, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"


@pytest.mark.parametrize("role", [UserRole.CITIZEN, UserRole.ADMIN])
def test_rol_no_operador_recibe_403_estable(user_factory, institution, report, role):
    user = user_factory(email=f"{role}-b2@example.com", role=role)

    response = _client(user).post(_url(report, "validate", institution), {}, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"


def test_anonimo_recibe_401(institution, report):
    response = _client().post(_url(report, "validate", institution), {}, format="json")

    assert response.status_code == 401


def test_institucion_inactiva_es_conflicto(operator_user, institution, report):
    institution.is_active = False
    institution.save(update_fields=["is_active", "updated_at"])

    response = _client(operator_user).post(_url(report, "validate", institution), {}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "INSTITUTION_INACTIVE"
    assert ReportStatusHistory.objects.count() == 0


def test_fallback_institucional_permite_validar(operator_user, institution, report):
    assert report.institution_id is None
    _creation_reward(report)

    response = _client(operator_user).post(_url(report, "validate", institution), {}, format="json")

    assert response.status_code == 200
    report.refresh_from_db()
    assert report.institution == institution


def test_sugerencia_ia_no_autoriza_otro_tenant(
    operator_user,
    institution,
    category,
):
    other = Institution.objects.create(name="ESSAP B2", slug="essap-b2")
    report = _report(category, institution=institution)
    AiClassification.objects.create(
        report=report,
        suggested_institution=other,
        suggested_category=category,
        suggested_priority="ALTA",
        confidence="0.99",
        inconsistency_flag=False,
        justification="Sugerencia no autoritativa.",
        model_name="fake",
    )

    response = _client(operator_user).post(_url(report, "validate", other), {}, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "INSTITUTION_OPERATOR_REQUIRED"
    report.refresh_from_db()
    assert report.validated_at is None


@pytest.mark.parametrize(
    ("action", "status_value", "validated", "expected_code"),
    [
        ("validate", ReportStatus.ASIGNADO, True, "ALREADY_VALIDATED"),
        ("reject", ReportStatus.RECHAZADO, False, "ALREADY_REJECTED"),
        ("reject", ReportStatus.ASIGNADO, True, "VALIDATION_REVERSAL_REQUIRED"),
        ("validate", ReportStatus.EN_PROCESO, False, "REPORT_STATE_NOT_ALLOWED"),
        ("reject", ReportStatus.RESUELTO, False, "REPORT_STATE_NOT_ALLOWED"),
    ],
)
def test_conflictos_de_dominio_se_proyectan_con_codigo_estable(
    operator_user,
    institution,
    report,
    action,
    status_value,
    validated,
    expected_code,
):
    report.status = status_value
    report.institution = institution
    if validated:
        report.validated_at = timezone.now()
        report.validated_by = operator_user
    report.save(
        update_fields=["status", "institution", "validated_at", "validated_by", "updated_at"]
    )
    payload = {} if action == "validate" else {"reason": RejectionReason.DUPLICADO}

    response = _client(operator_user).post(
        _url(report, action, institution), payload, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == expected_code
    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_rollback_no_invalida_cache(
    monkeypatch,
    operator_user,
    institution,
    report,
    django_capture_on_commit_callbacks,
):
    invalidations = []
    monkeypatch.setattr(
        validation,
        "invalidate_public_reports_cache",
        lambda: invalidations.append("called"),
    )
    monkeypatch.setattr(
        validation.ledger,
        "on_report_validated",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ledger roto")),
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = _client(operator_user).post(
            _url(report, "validate", institution), {}, format="json"
        )

    assert response.status_code == 500
    assert callbacks == []
    assert invalidations == []
    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO


def test_invalidacion_de_cache_es_post_commit_y_best_effort(
    monkeypatch,
    caplog,
    operator_user,
    institution,
    report,
    django_capture_on_commit_callbacks,
):
    _creation_reward(report)

    def fail():
        raise RuntimeError("redis://usuario:secreto@host")

    monkeypatch.setattr(validation, "invalidate_public_reports_cache", fail)

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(operator_user).post(
            _url(report, "validate", institution), {}, format="json"
        )

    assert response.status_code == 200
    report.refresh_from_db()
    assert report.status == ReportStatus.ASIGNADO
    assert "No se pudo invalidar el mapa público" in caplog.text
    assert "secreto" not in caplog.text


def _concurrency_context():
    institution = Institution.objects.create(name="Institución HTTP", slug="institucion-http")
    category = Category.objects.create(
        name="Categoría HTTP",
        slug="categoria-http",
        default_institution=institution,
    )
    users = get_user_model().objects
    author = users.create_user(email="autor-http@example.test")
    operator = users.create_user(email="operador-http@example.test", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=operator, institution=institution, active=True)
    report = _report(category, citizen=author)
    _creation_reward(report)
    return report, operator, institution


@pytest.mark.django_db(transaction=True)
def test_doble_validate_http_concurrente_deja_un_solo_agregado():
    report, operator, institution = _concurrency_context()
    barrier = threading.Barrier(2)

    def attempt(_):
        connections.close_all()
        barrier.wait(timeout=5)
        try:
            thread_operator = get_user_model().objects.get(pk=operator.pk)
            return _client(thread_operator).post(
                _url(report, "validate", institution), {}, format="json"
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["code"] == "ALREADY_VALIDATED"
    assert (
        RewardTransaction.objects.filter(event_type=RewardEventType.VALIDATION_BONUS).count() == 1
    )
    assert ReportStatusHistory.objects.count() == 1
    assert AuditLog.objects.filter(action="report.validated").count() == 1


@pytest.mark.django_db(transaction=True)
def test_revocacion_concurrente_confirmada_antes_de_decision_la_rechaza():
    report, operator_user, institution = _concurrency_context()
    created = RewardTransaction.objects.get(event_type=RewardEventType.REPORT_CREATED)
    barrier = threading.Barrier(2)

    def revoke():
        connections.close_all()
        try:
            with transaction.atomic():
                membership = InstitutionOperator.objects.select_for_update().get(
                    user_id=operator_user.pk,
                    institution_id=institution.pk,
                )
                membership.active = False
                membership.save(update_fields=["active", "updated_at"])
                barrier.wait(timeout=5)
            return "REVOKED"
        finally:
            connections.close_all()

    def decide():
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            operator = get_user_model().objects.get(pk=operator_user.pk)
            response = _client(operator).post(
                _url(report, "validate", institution), {}, format="json"
            )
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        revocation = pool.submit(revoke)
        decision = pool.submit(decide)
        assert revocation.result() == "REVOKED"
        decision_status, decision_payload = decision.result()

    assert decision_status == 403
    assert decision_payload["code"] == "INSTITUTION_OPERATOR_REQUIRED"
    report.refresh_from_db()
    created.refresh_from_db()
    assert report.status == ReportStatus.CREADO
    assert report.validated_at is None
    assert created.status == RewardTxStatus.PENDIENTE
    assert ReportStatusHistory.objects.count() == 0
    assert AuditLog.objects.count() == 0
