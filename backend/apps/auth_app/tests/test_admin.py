from django.contrib.admin.sites import AdminSite

from apps.auth_app.admin import CitizenProfileAdmin
from apps.auth_app.models import CitizenProfile

SENSIBLES = {"document_hmac", "verification_selfie_url"}


def _admin():
    return CitizenProfileAdmin(CitizenProfile, AdminSite())


def test_los_campos_sensibles_no_se_listan_ni_se_buscan():
    admin = _admin()

    assert SENSIBLES.isdisjoint(admin.list_display)
    assert SENSIBLES.isdisjoint(admin.search_fields)


def test_los_campos_sensibles_son_de_solo_lectura():
    from apps.core.tests.fake_request import FakeRequest

    admin = _admin()

    assert SENSIBLES.issubset(set(admin.readonly_fields))
    assert SENSIBLES.issubset(set(admin.get_readonly_fields(FakeRequest())))
