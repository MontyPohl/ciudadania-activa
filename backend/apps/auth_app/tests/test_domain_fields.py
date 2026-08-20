import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.auth_app.enums import RankingVisibility, UserRole, VerificationStatus

pytestmark = pytest.mark.django_db


def test_usuario_nuevo_nace_ciudadano_pendiente_de_verificacion(citizen):
    assert citizen.role == UserRole.CITIZEN
    assert citizen.verification_status == VerificationStatus.PENDIENTE
    assert citizen.google_oauth_id is None


def test_roles_y_estados_de_verificacion_disponibles():
    assert UserRole.values == ["citizen", "operator", "admin"]
    assert VerificationStatus.values == ["PENDIENTE", "APROBADO", "RECHAZADO"]
    assert RankingVisibility.values == ["FULL_NAME", "FIRST_INITIAL", "ALIAS", "PRIVATE"]


def test_google_oauth_id_es_unico(user_factory):
    user_factory(email="a@example.com", google_oauth_id="google-1")

    with pytest.raises(IntegrityError), transaction.atomic():
        user_factory(email="b@example.com", google_oauth_id="google-1")


def test_varios_usuarios_pueden_no_tener_google_oauth_id(user_factory):
    user_factory(email="a@example.com")
    user_factory(email="b@example.com")

    assert get_user_model().objects.filter(google_oauth_id__isnull=True).count() == 2


def test_auth_user_model_sigue_siendo_el_del_sprint_1():
    User = get_user_model()

    assert User._meta.label == "auth_app.User"
    assert User._meta.db_table == "users"
    assert User._meta.pk.get_internal_type() == "UUIDField"
