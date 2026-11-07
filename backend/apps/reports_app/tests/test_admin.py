import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.ai_client_app.admin import AiClassificationAdmin
from apps.ai_client_app.models import AiClassification
from apps.core.enums import ReportStatus
from apps.core.tests.fake_request import FakeRequest
from apps.reports_app.admin import (
    ReportAdmin,
    ReportConfirmationAdmin,
    ReportMediaAdmin,
    ReportStatusHistoryAdmin,
    ResolutionEvidenceAdmin,
)
from apps.reports_app.models import (
    Report,
    ReportConfirmation,
    ReportMedia,
    ReportStatusHistory,
    ResolutionEvidence,
)


def test_reportes_confirmaciones_e_historial_son_read_only_estrictos():
    request = FakeRequest()

    for admin_class, model in (
        (ReportAdmin, Report),
        (ReportConfirmationAdmin, ReportConfirmation),
        (ReportStatusHistoryAdmin, ReportStatusHistory),
        (ResolutionEvidenceAdmin, ResolutionEvidence),
        (ReportMediaAdmin, ReportMedia),
        (AiClassificationAdmin, AiClassification),
    ):
        model_admin = admin_class(model, AdminSite())
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_delete_permission(request) is False
        assert model_admin.has_view_permission(request) is True
        assert set(model_admin.get_readonly_fields(request)) == {
            field.name for field in model._meta.fields
        }


@pytest.mark.django_db
def test_el_reporte_se_inspecciona_y_no_se_modifica_desde_el_admin(report):
    """`ReportAdmin` es geográfico: el punto también debe mostrarse sin el widget del mapa."""
    superuser = get_user_model().objects.create_superuser(
        email="tecnico@example.test", password="clave-de-prueba"
    )
    client = Client()
    client.force_login(superuser)
    url = reverse("admin:reports_app_report_change", args=[report.pk])

    pagina = client.get(url)
    intento = client.post(url, {"status": ReportStatus.CERRADO})

    assert pagina.status_code == 200
    assert 'name="status"' not in pagina.content.decode()
    assert intento.status_code == 403
    report.refresh_from_db()
    assert report.status == ReportStatus.CREADO
