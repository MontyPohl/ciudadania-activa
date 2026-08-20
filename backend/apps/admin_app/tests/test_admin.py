from django.contrib.admin.sites import AdminSite

from apps.admin_app.admin import AuditLogAdmin
from apps.admin_app.models import AuditLog
from apps.core.tests.fake_request import FakeRequest


def test_la_auditoria_es_de_solo_lectura_en_el_admin():
    admin = AuditLogAdmin(AuditLog, AdminSite())
    request = FakeRequest()

    assert admin.has_add_permission(request) is False
    assert admin.has_change_permission(request) is False
    assert admin.has_delete_permission(request) is False
    assert admin.has_view_permission(request) is True
    assert set(admin.get_readonly_fields(request)) == {
        field.name for field in AuditLog._meta.fields
    }
