"""Escenarios del Anexo H.2, C-05 y CT-22 según la matriz del S14 (02 §1).

El mundo H.2 recorre servicios reales; los saldos se consultan con selectores.
LED-01 contrasta la API con SQL literal, sin insertar movimientos a mano.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auth_app.enums import VerificationStatus
from apps.core.enums import RejectionReason, ReportStatus
from apps.reports_app.services import (
    confirmations,
    lifecycle,
    reports,
    resolution_evidence,
    reversal,
    validation,
)
from apps.rewards_app import selectors
from apps.rewards_app.enums import RewardEventType, RewardTxStatus
from apps.rewards_app.models import RewardTransaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def mundo_h2(
    user_factory,
    operator_user,
    admin_user,
    institution,
    category,
    storage_config,
    fake_storage,
    jpeg_bytes,
    png_bytes,
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    now = timezone.now()
    # Servicios y consultas comparten el instante incluso en el borde del mes.
    monkeypatch.setattr(timezone, "now", lambda: now)
    assert institution.slug == "municipalidad-de-asuncion"
    assert category.default_institution == institution
    return SimpleNamespace(
        citizen_a=user_factory(
            email="a-s14@example.test",
            full_name="Ciudadana A",
            verification_status=VerificationStatus.APROBADO,
        ),
        citizen_b=user_factory(
            email="b-s14@example.test",
            full_name="Ciudadano B",
            verification_status=VerificationStatus.APROBADO,
        ),
        operator=operator_user,
        admin=admin_user,
        institution=institution,
        category=category,
        jpeg_bytes=jpeg_bytes,
        png_bytes=png_bytes,
        now=now,
        on_commit=django_capture_on_commit_callbacks,
    )


def _crear_reporte(mundo, citizen, codigo, photo):
    with mundo.on_commit(execute=True):
        return reports.create_report(
            citizen=citizen,
            category=mundo.category,
            description=f"{codigo}: bache profundo en la calzada.",
            photo=photo,
            latitude=-25.2637,
            longitude=-57.6359,
        ).report


def _crear_r001(mundo):
    return _crear_reporte(
        mundo,
        mundo.citizen_a,
        "R-001",
        SimpleUploadedFile("r001.jpg", mundo.jpeg_bytes, content_type="image/jpeg"),
    )


def _crear_r002(mundo, citizen):
    # Una foto distinta evita reutilizar el hash global de R-001.
    return _crear_reporte(
        mundo,
        citizen,
        "R-002",
        SimpleUploadedFile("r002.png", mundo.png_bytes, content_type="image/png"),
    )


def _confirmar(report, citizen):
    return confirmations.confirm_report(report_id=report.pk, citizen=citizen)


def _validar(mundo, report):
    with mundo.on_commit(execute=True):
        return validation.validate_report(
            report_id=report.pk,
            operator=mundo.operator,
            institution=mundo.institution,
        )


def _resolver(mundo, report):
    with mundo.on_commit(execute=True):
        lifecycle.transition_report_status(
            report_id=report.pk,
            operator_id=mundo.operator.pk,
            institution_id=mundo.institution.pk,
            target_status=ReportStatus.EN_PROCESO,
            expected_cycle=1,
        )
        resolution_evidence.create_resolution_evidence(
            report_id=report.pk,
            operator_id=mundo.operator.pk,
            institution_id=mundo.institution.pk,
            expected_cycle=1,
            idempotency_key=uuid.uuid4(),
            description="Reparación terminada, sin datos personales.",
            uploaded_file=SimpleUploadedFile(
                "resolucion.png", mundo.png_bytes, content_type="image/png"
            ),
        )
        return lifecycle.transition_report_status(
            report_id=report.pk,
            operator_id=mundo.operator.pk,
            institution_id=mundo.institution.pk,
            target_status=ReportStatus.RESUELTO,
            expected_cycle=1,
        )


def _revertir(mundo, report):
    with mundo.on_commit(execute=True):
        return reversal.revert_report_validation(
            report_id=report.pk,
            admin_id=mundo.admin.pk,
            expected_cycle=1,
            reason="Corrección administrativa de la validación de R-001.",
        )


def _rechazar(mundo, report):
    with mundo.on_commit(execute=True):
        return validation.reject_report(
            report_id=report.pk,
            operator=mundo.operator,
            institution=mundo.institution,
            reason=RejectionReason.DUPLICADO,
        )


def _movimientos_del_reporte(report):
    # Las confirmaciones apuntan al reporte mediante su propia FK.
    return RewardTransaction.objects.filter(Q(report=report) | Q(confirmation__report=report))


def _comparar_api_y_sql(citizens):
    for citizen in citizens:
        client = APIClient()
        client.force_authenticate(citizen)
        response = client.get("/api/v1/rewards/summary/")
        assert response.status_code == 200
        payload = response.json()

        # El esperado no usa ORM ni selectores: agrega la tabla real del ledger.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(impact_delta), 0)
                FROM reward_transactions
                WHERE user_id = %s AND status = 'ACREDITADO'
                """,
                [citizen.pk],
            )
            historical = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT COALESCE(SUM(impact_delta), 0)
                FROM reward_transactions
                WHERE user_id = %s AND status = 'PENDIENTE'
                """,
                [citizen.pk],
            )
            pending = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT institution_id, SUM(credit_delta)
                FROM reward_transactions
                WHERE user_id = %s AND status = 'ACREDITADO'
                    AND institution_id IS NOT NULL
                GROUP BY institution_id
                """,
                [citizen.pk],
            )
            balances = {
                str(institution_id): balance for institution_id, balance in cursor.fetchall()
            }

        assert payload["impact"]["historical"] == historical
        assert payload["impact"]["pending"] == pending
        assert {
            row["institution"]["id"]: row["balance"] for row in payload["credits_by_institution"]
        } == balances


def test_s01_crear_reporte_suma_20_pendientes_sin_creditos(mundo_h2):
    mundo = mundo_h2
    _crear_r001(mundo)

    assert selectors.impact_summary(user_id=mundo.citizen_a.pk, now=mundo.now) == (
        selectors.ImpactSummary(historical=0, current_month=0, pending=20)
    )
    assert selectors.institution_balances(user_id=mundo.citizen_a.pk) == []


def test_s02_validar_acredita_50_y_50_creditos_de_la_institucion(mundo_h2):
    mundo = mundo_h2
    report = _crear_r001(mundo)
    _validar(mundo, report)

    assert selectors.impact_summary(user_id=mundo.citizen_a.pk, now=mundo.now) == (
        selectors.ImpactSummary(historical=50, current_month=50, pending=0)
    )
    assert (
        selectors.institution_balance(
            user_id=mundo.citizen_a.pk, institution_id=mundo.institution.pk
        )
        == 50
    )


def test_s03_confirmacion_previa_queda_pendiente_y_se_acredita_al_validar(mundo_h2):
    mundo = mundo_h2
    report = _crear_r001(mundo)
    _confirmar(report, mundo.citizen_b)

    assert selectors.impact_summary(user_id=mundo.citizen_b.pk, now=mundo.now) == (
        selectors.ImpactSummary(historical=0, current_month=0, pending=10)
    )
    assert selectors.institution_balances(user_id=mundo.citizen_b.pk) == []

    _validar(mundo, report)

    assert selectors.impact_summary(user_id=mundo.citizen_b.pk, now=mundo.now) == (
        selectors.ImpactSummary(historical=10, current_month=10, pending=0)
    )
    assert (
        selectors.institution_balance(
            user_id=mundo.citizen_b.pk, institution_id=mundo.institution.pk
        )
        == 10
    )


def test_s04_resolver_suma_50_y_el_reporte_propio_totaliza_100(mundo_h2):
    mundo = mundo_h2
    report = _crear_r001(mundo)
    _validar(mundo, report)
    impact_before = selectors.impact_summary(user_id=mundo.citizen_a.pk, now=mundo.now)
    balance_before = selectors.institution_balance(
        user_id=mundo.citizen_a.pk, institution_id=mundo.institution.pk
    )
    assert impact_before.historical == balance_before == 50

    _resolver(mundo, report)

    impact_after = selectors.impact_summary(user_id=mundo.citizen_a.pk, now=mundo.now)
    balance_after = selectors.institution_balance(
        user_id=mundo.citizen_a.pk, institution_id=mundo.institution.pk
    )
    assert impact_after == selectors.ImpactSummary(historical=100, current_month=100, pending=0)
    assert balance_after == 100
    assert impact_after.historical - impact_before.historical == 50
    assert balance_after - balance_before == 50
    assert (
        RewardTransaction.objects.filter(
            user=mundo.citizen_a, report=report, status=RewardTxStatus.ACREDITADO
        ).aggregate(total=Sum("impact_delta"))["total"]
        == 100
    )


def test_s07_revertir_retira_todo_lo_acreditado_del_reporte(mundo_h2):
    mundo = mundo_h2
    report = _crear_r001(mundo)
    _confirmar(report, mundo.citizen_b)
    _validar(mundo, report)
    _resolver(mundo, report)
    movimientos = _movimientos_del_reporte(report)
    originales = set(
        movimientos.filter(status=RewardTxStatus.ACREDITADO).values_list("id", flat=True)
    )
    assert len(originales) == 4
    for citizen, expected in [(mundo.citizen_a, 100), (mundo.citizen_b, 10)]:
        assert selectors.impact_summary(user_id=citizen.pk, now=mundo.now).historical == expected
        assert (
            selectors.institution_balance(user_id=citizen.pk, institution_id=mundo.institution.pk)
            == expected
        )

    _revertir(mundo, report)

    for citizen in (mundo.citizen_a, mundo.citizen_b):
        assert selectors.impact_summary(user_id=citizen.pk, now=mundo.now) == (
            selectors.ImpactSummary(historical=0, current_month=0, pending=0)
        )
        assert (
            selectors.institution_balance(user_id=citizen.pk, institution_id=mundo.institution.pk)
            == 0
        )
        assert selectors.institution_balances(user_id=citizen.pk) == []
    assert not movimientos.filter(
        status__in=[RewardTxStatus.ACREDITADO, RewardTxStatus.PENDIENTE]
    ).exists()
    assert (
        not movimientos.filter(pk__in=originales).exclude(status=RewardTxStatus.REVERTIDO).exists()
    )
    compensaciones = movimientos.filter(event_type=RewardEventType.REVERSAL)
    assert compensaciones.count() == len(originales)
    assert set(compensaciones.values_list("related_transaction_id", flat=True)) == originales


def test_s11_rechazar_anula_todos_los_pendientes_del_reporte(mundo_h2):
    mundo = mundo_h2
    report = _crear_r002(mundo, mundo.citizen_a)
    _confirmar(report, mundo.citizen_b)
    for citizen, pending in [(mundo.citizen_a, 20), (mundo.citizen_b, 10)]:
        assert selectors.impact_summary(user_id=citizen.pk, now=mundo.now) == (
            selectors.ImpactSummary(historical=0, current_month=0, pending=pending)
        )
        assert selectors.institution_balances(user_id=citizen.pk) == []

    _rechazar(mundo, report)

    movimientos = _movimientos_del_reporte(report)
    assert movimientos.count() == 2
    assert not movimientos.exclude(status=RewardTxStatus.ANULADO).exists()
    assert not movimientos.filter(voided_at__isnull=True).exists()
    assert not movimientos.filter(status=RewardTxStatus.PENDIENTE).exists()
    for citizen in (mundo.citizen_a, mundo.citizen_b):
        assert selectors.impact_summary(user_id=citizen.pk, now=mundo.now) == (
            selectors.ImpactSummary(historical=0, current_month=0, pending=0)
        )
        assert selectors.institution_balances(user_id=citizen.pk) == []
        assert (
            selectors.institution_balance(user_id=citizen.pk, institution_id=mundo.institution.pk)
            == 0
        )


def test_saldos_de_la_api_coinciden_con_la_agregacion_sql(mundo_h2):
    mundo = mundo_h2
    citizens = (mundo.citizen_a, mundo.citizen_b)
    report = _crear_r001(mundo)
    _confirmar(report, mundo.citizen_b)
    _comparar_api_y_sql(citizens)

    _validar(mundo, report)
    _resolver(mundo, report)
    rechazado = _crear_r002(mundo, mundo.citizen_b)
    _rechazar(mundo, rechazado)
    # §A2 permite omitir el canje: alcanzar 1.000 exige diez reportes resueltos.
    _comparar_api_y_sql(citizens)

    _revertir(mundo, report)
    _comparar_api_y_sql(citizens)
