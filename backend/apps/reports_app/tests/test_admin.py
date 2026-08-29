from django.contrib.admin.sites import AdminSite

from apps.core.tests.fake_request import FakeRequest
from apps.reports_app.admin import ReportConfirmationAdmin, ReportStatusHistoryAdmin
from apps.reports_app.models import ReportConfirmation, ReportStatusHistory


def test_confirmaciones_e_historial_son_read_only_estrictos():
    request = FakeRequest()

    for admin_class, model in (
        (ReportConfirmationAdmin, ReportConfirmation),
        (ReportStatusHistoryAdmin, ReportStatusHistory),
    ):
        model_admin = admin_class(model, AdminSite())
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_delete_permission(request) is False
        assert model_admin.has_view_permission(request) is True
        assert set(model_admin.get_readonly_fields(request)) == {
            field.name for field in model._meta.fields
        }
