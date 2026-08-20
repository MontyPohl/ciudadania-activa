import pytest
from django.db import IntegrityError, transaction

from apps.auth_app.enums import RankingVisibility
from apps.auth_app.models import CitizenProfile

pytestmark = pytest.mark.django_db


def _perfil(user, hmac="a" * 64):
    return CitizenProfile.objects.create(user=user, document_hmac=hmac)


def test_perfil_valido_con_preferencias_por_defecto(citizen):
    perfil = _perfil(citizen)

    assert perfil.ranking_visibility == RankingVisibility.FULL_NAME
    assert perfil.ranking_alias == ""
    assert perfil.verified_at is None
    assert perfil.verified_by is None
    assert str(perfil) == f"Perfil de {citizen.pk}"


def test_document_hmac_es_obligatorio(citizen):
    with pytest.raises(IntegrityError), transaction.atomic():
        CitizenProfile.objects.create(user=citizen, document_hmac=None)


def test_document_hmac_es_unico(citizen, other_citizen):
    _perfil(citizen, hmac="b" * 64)

    with pytest.raises(IntegrityError), transaction.atomic():
        _perfil(other_citizen, hmac="b" * 64)


def test_el_perfil_no_se_crea_solo_con_el_usuario(citizen):
    """D2-06: el perfil nace al enviar la verificación de identidad (Sprint 4)."""
    assert not CitizenProfile.objects.filter(user=citizen).exists()


def test_un_usuario_tiene_como_maximo_un_perfil(citizen):
    _perfil(citizen, hmac="c" * 64)

    with pytest.raises(IntegrityError), transaction.atomic():
        _perfil(citizen, hmac="d" * 64)


def test_borrar_el_usuario_arrastra_su_perfil(citizen):
    _perfil(citizen, hmac="e" * 64)
    citizen.delete()

    assert CitizenProfile.objects.count() == 0


def test_verified_by_queda_en_null_si_se_borra_el_revisor(citizen, other_citizen):
    perfil = CitizenProfile.objects.create(
        user=citizen, document_hmac="f" * 64, verified_by=other_citizen
    )
    other_citizen.delete()
    perfil.refresh_from_db()

    assert perfil.verified_by is None
    assert CitizenProfile.objects.count() == 1
