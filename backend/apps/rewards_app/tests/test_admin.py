from django.contrib.admin.sites import AdminSite

from apps.core.tests.fake_request import FakeRequest
from apps.rewards_app.admin import RewardAdmin, RewardRedemptionAdmin, RewardTransactionAdmin
from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction


def test_el_ledger_es_de_solo_lectura_aunque_se_tengan_todos_los_permisos():
    admin = RewardTransactionAdmin(RewardTransaction, AdminSite())
    request = FakeRequest()

    assert admin.has_add_permission(request) is False
    assert admin.has_change_permission(request) is False
    assert admin.has_delete_permission(request) is False
    # La inspección exigida por el Sprint 2 sigue disponible por el permiso de vista.
    assert admin.has_view_permission(request) is True


def test_ningun_campo_del_ledger_es_editable_en_el_admin():
    admin = RewardTransactionAdmin(RewardTransaction, AdminSite())
    readonly = set(admin.get_readonly_fields(FakeRequest()))

    for campo in ("impact_delta", "credit_delta", "event_type", "status", "institution"):
        assert campo in readonly
    for marca in ("credited_at", "voided_at", "reversed_at"):
        assert marca in readonly


def test_los_canjes_son_de_solo_lectura_en_el_admin():
    admin = RewardRedemptionAdmin(RewardRedemption, AdminSite())
    request = FakeRequest()

    assert admin.has_add_permission(request) is False
    assert admin.has_change_permission(request) is False
    assert admin.has_delete_permission(request) is False
    assert {"code", "credits_spent"} <= set(admin.get_readonly_fields(request))


def test_el_costo_de_la_recompensa_no_es_editable_desde_el_admin():
    admin = RewardAdmin(Reward, AdminSite())

    assert "credit_cost" in admin.readonly_fields
    # El campo sigue siendo estructuralmente normal: no se usa `editable=False`.
    assert Reward._meta.get_field("credit_cost").editable is True
    assert Reward._meta.get_field("credit_cost").default == 1000


def test_la_recompensa_sigue_siendo_administrable_en_lo_demas():
    admin = RewardAdmin(Reward, AdminSite())
    request = FakeRequest()

    assert admin.has_change_permission(request) is True
    assert admin.has_add_permission(request) is True
    assert "name" not in admin.readonly_fields
    assert "active" not in admin.readonly_fields
