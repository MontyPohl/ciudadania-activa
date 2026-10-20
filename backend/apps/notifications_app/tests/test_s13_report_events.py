"""Avisos por correo de los eventos de un reporte (Sprint 13, S13-D09).

Se recorren los servicios **reales** de `reports_app`. Los helpers de transición y
evidencia se copian de `reports_app/tests/test_lifecycle_service.py`, con el mismo
criterio que S12: no se comparten entre aplicaciones de prueba. La creación se verifica
en `test_create_service.py`, porque sus fixtures de foto y Storage son locales.
"""

import uuid

import pytest
from django.utils import timezone

from apps.admin_app.models import Institution
from apps.core.enums import RejectionReason, ReportStatus
from apps.notifications_app.enums import NotificationStatus, NotificationType
from apps.notifications_app.models import Notification
from apps.notifications_app.services import dispatch
from apps.notifications_app.services.events import notify_report_event
from apps.reports_app.models import Report, ResolutionEvidence
from apps.reports_app.services import lifecycle, reversal, validation
from apps.rewards_app.services import ledger

pytestmark = pytest.mark.django_db


def _validate(report, operator_user, institution):
    validation.validate_report(report_id=report.pk, operator=operator_user, institution=institution)
    report.refresh_from_db()


def _transition(report, operator_user, institution, status, cycle=1):
    return lifecycle.transition_report_status(
        report_id=report.pk,
        operator_id=operator_user.pk,
        institution_id=institution.pk,
        target_status=status,
        expected_cycle=cycle,
    )


def _evidence(report, operator_user, cycle=1):
    return ResolutionEvidence.objects.create(
        report=report,
        description="Trabajo concluido.",
        evidence_url=f"resolution-evidence/{report.pk}/{cycle}/{uuid.uuid4()}.jpg",
        uploaded_by=operator_user,
        resolution_cycle=cycle,
        idempotency_key=uuid.uuid4(),
        content_hash="a" * 64,
    )


@pytest.mark.parametrize("evento", ["validar", "rechazar", "resolver"])
def test_cada_evento_notifica_una_vez_al_autor(
    evento, report, operator_user, institution, admin_user
):
    ledger.on_report_created(report)
    if evento == "rechazar":
        validation.reject_report(
            report_id=report.pk,
            operator=operator_user,
            institution=institution,
            reason=RejectionReason.DUPLICADO,
        )
        tipo, ciclo = NotificationType.REPORTE_RECHAZADO, None
    else:
        _validate(report, operator_user, institution)
        tipo, ciclo = NotificationType.VALIDACION_INSTITUCIONAL, 1
        if evento == "resolver":
            _transition(report, operator_user, institution, ReportStatus.EN_PROCESO)
            _evidence(report, operator_user)
            _transition(report, operator_user, institution, ReportStatus.RESUELTO)
            tipo = NotificationType.REPORTE_RESUELTO

    aviso = Notification.objects.get(notification_type=tipo)
    clave = f"{tipo.value}:{report.pk}" + (f":{ciclo}" if ciclo is not None else "")
    assert aviso.user == report.citizen
    assert aviso.report_id == report.pk
    assert aviso.institution == institution
    assert aviso.dedup_key == clave
    assert aviso.status == NotificationStatus.PENDIENTE

    # El mismo evento no produce un segundo aviso.
    report.refresh_from_db()
    assert notify_report_event(report, tipo, institution=institution, cycle=ciclo) is None
    assert Notification.objects.filter(notification_type=tipo).count() == 1

    if evento == "validar":
        # Reversión y revalidación: es otro ciclo y, por lo tanto, otro aviso.
        reversal.revert_report_validation(
            report_id=report.pk,
            admin_id=admin_user.pk,
            expected_cycle=1,
            reason="Reversión de prueba por una validación errónea.",
        )
        _validate(report, operator_user, institution)
        claves = set(
            Notification.objects.filter(notification_type=tipo).values_list("dedup_key", flat=True)
        )
        assert claves == {f"{tipo.value}:{report.pk}:1", f"{tipo.value}:{report.pk}:2"}


@pytest.mark.parametrize("caso", ["sin_autor", "autor_inactivo"])
def test_reporte_sin_autor_o_autor_inactivo_no_notifica(caso, report, operator_user, institution):
    if caso == "sin_autor":
        # Como los reportes de X simulado: sin ciudadano y sin REPORT_CREATED (RF-45).
        Report.objects.filter(pk=report.pk).update(citizen=None)
        report.refresh_from_db()
    else:
        ledger.on_report_created(report)
        report.citizen.is_active = False
        report.citizen.save(update_fields=["is_active"])

    _validate(report, operator_user, institution)

    assert Notification.objects.count() == 0


@pytest.mark.parametrize(
    "tipo",
    [
        NotificationType.REPORTE_CREADO,
        NotificationType.VALIDACION_INSTITUCIONAL,
        NotificationType.REPORTE_RESUELTO,
        NotificationType.REPORTE_RECHAZADO,
        NotificationType.RECOMPENSA_DISPONIBLE,
    ],
)
def test_plantillas_nuevas_sin_datos_sensibles(tipo, report, brevo_api):
    """Contenido mínimo: sin descripción, ubicación, email ni HTML escapado."""
    institucion = Institution.objects.create(
        name="Agua & Saneamiento (prueba)", slug="agua-saneamiento-prueba"
    )
    es_de_reporte = tipo != NotificationType.RECOMPENSA_DISPONIBLE
    aviso = dispatch.create(
        report.citizen,
        tipo,
        report=report if es_de_reporte else None,
        institution=None if tipo == NotificationType.REPORTE_CREADO else institucion,
    )

    assert dispatch.send(aviso.pk).status == NotificationStatus.ENVIADO

    correo = brevo_api.ultimo
    cuerpo = correo.cuerpo
    asunto, _ = dispatch.PLANTILLAS[tipo]
    assert correo.asunto == asunto
    assert report.description not in cuerpo
    assert report.citizen.email not in cuerpo
    assert str(report.location.y)[:6] not in cuerpo
    assert "&amp;" not in cuerpo
    assert "motivo" not in cuerpo.lower()
    if es_de_reporte:
        assert timezone.localtime(report.created_at).strftime("%d/%m/%Y") in cuerpo
    if tipo == NotificationType.REPORTE_CREADO:
        # Sin categoría: la IA puede reclasificar el reporte mientras sale el correo.
        assert report.category.name not in cuerpo
        assert institucion.name not in cuerpo
    else:
        assert "Agua & Saneamiento (prueba)" in cuerpo
        if es_de_reporte:
            assert report.category.name in cuerpo
        else:
            assert "no tiene valor económico real" in cuerpo
