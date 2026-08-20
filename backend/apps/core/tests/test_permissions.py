"""Permisos reutilizables y política de denegación por defecto."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import Institution, InstitutionOperator
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.core import permissions

User = get_user_model()


class _Objeto:
    """Objeto mínimo con institución, como los reportes del Sprint 9."""

    def __init__(self, institution_id=None):
        self.institution_id = institution_id


class _SinInstitucion:
    pass


def _peticion(user=None):
    request = APIRequestFactory().get("/")
    request.user = user if user is not None else _Anonimo()
    return request


class _Anonimo:
    is_authenticated = False
    role = None


# --------------------------------------------------------------------------- #
# DenyAll
# --------------------------------------------------------------------------- #


def test_deny_all_no_concede_a_nadie(db, citizen, admin_user):
    permiso = permissions.DenyAll()

    for user in (None, citizen, admin_user):
        assert permiso.has_permission(_peticion(user), None) is False
        assert permiso.has_object_permission(_peticion(user), None, _Objeto()) is False


def test_deny_all_tampoco_concede_a_un_superusuario(db, user_factory):
    superusuario = User.objects.create_superuser(
        email="super@example.com", password="clave-segura-123"
    )

    assert permissions.DenyAll().has_permission(_peticion(superusuario), None) is False


def test_deny_all_es_el_permiso_por_defecto_de_la_api(settings):
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "apps.core.permissions.DenyAll"
    ]


def test_una_vista_sin_permiso_declarado_no_responde(db, citizen):
    """Comprueba la política, sin fijar un código concreto: 401 o 403 según autenticador."""
    from rest_framework.views import APIView

    class VistaOlvidadiza(APIView):
        def get(self, request):  # pragma: no cover - no debe alcanzarse
            raise AssertionError("la vista no debería ejecutarse")

    factory = APIRequestFactory()
    respuesta = VistaOlvidadiza.as_view()(factory.get("/"))

    assert respuesta.status_code in (401, 403)
    assert respuesta.status_code != 200


def test_el_health_publico_sigue_accesible(db, client):
    """La única vista existente declara AllowAny y no se ve afectada."""
    assert client.get("/api/v1/health/").status_code == 200


# --------------------------------------------------------------------------- #
# IsAuthenticated
# --------------------------------------------------------------------------- #


def test_is_authenticated_se_reexporta_de_drf():
    from rest_framework.permissions import IsAuthenticated as DrfIsAuthenticated

    assert permissions.IsAuthenticated is DrfIsAuthenticated


# --------------------------------------------------------------------------- #
# IsVerifiedCitizen
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_is_verified_citizen_exige_ciudadano_aprobado(user_factory, citizen, operator_user):
    permiso = permissions.IsVerifiedCitizen()
    verificado = user_factory(
        email="verificado@example.com", verification_status=VerificationStatus.APROBADO
    )

    assert permiso.has_permission(_peticion(verificado), None) is True
    assert permiso.has_permission(_peticion(citizen), None) is False  # PENDIENTE
    assert permiso.has_permission(_peticion(None), None) is False


@pytest.mark.django_db
def test_un_operador_aprobado_no_es_ciudadano_verificado(user_factory):
    """El permiso es de ciudadanía verificada, no de "estar aprobado"."""
    operador = user_factory(
        email="op-aprobado@example.com",
        role=UserRole.OPERATOR,
        verification_status=VerificationStatus.APROBADO,
    )

    assert permissions.IsVerifiedCitizen().has_permission(_peticion(operador), None) is False


@pytest.mark.django_db
def test_google_no_otorga_ciudadania_verificada(google_verifier, google_claims):
    """Iniciar sesión con Google deja al usuario en PENDIENTE (RF-06)."""
    from apps.auth_app.services import accounts as accounts_service
    from apps.auth_app.services.google import verify_id_token

    google_verifier(claims=google_claims())
    user = accounts_service.login_with_google(verify_id_token("id-token"))

    assert user.verification_status == VerificationStatus.PENDIENTE
    assert permissions.IsVerifiedCitizen().has_permission(_peticion(user), None) is False


# --------------------------------------------------------------------------- #
# IsInstitutionOperator
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_is_institution_operator_exige_vinculo_activo(operator_user, user_factory, citizen):
    permiso = permissions.IsInstitutionOperator()
    sin_vinculo = user_factory(email="operador-suelto@example.com", role=UserRole.OPERATOR)

    assert permiso.has_permission(_peticion(operator_user), None) is True
    assert permiso.has_permission(_peticion(sin_vinculo), None) is False
    assert permiso.has_permission(_peticion(citizen), None) is False


@pytest.mark.django_db
def test_un_vinculo_desactivado_no_habilita(operator_user):
    InstitutionOperator.objects.filter(user=operator_user).update(active=False)

    permiso = permissions.IsInstitutionOperator()

    assert permiso.has_permission(_peticion(operator_user), None) is False


# --------------------------------------------------------------------------- #
# IsAdmin
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_is_admin_exige_rol_de_dominio(admin_user, operator_user, citizen):
    permiso = permissions.IsAdmin()

    assert permiso.has_permission(_peticion(admin_user), None) is True
    assert permiso.has_permission(_peticion(operator_user), None) is False
    assert permiso.has_permission(_peticion(citizen), None) is False


@pytest.mark.django_db
def test_is_admin_no_se_deduce_de_is_staff_ni_is_superuser(user_factory):
    """`role` gobierna la API; los flags de Django gobiernan su admin (ADR-0005)."""
    staff = user_factory(email="staff@example.com", is_staff=True)
    superusuario = User.objects.create_superuser(
        email="super2@example.com", password="clave-segura-123"
    )

    assert permissions.IsAdmin().has_permission(_peticion(staff), None) is False
    assert permissions.IsAdmin().has_permission(_peticion(superusuario), None) is False


@pytest.mark.django_db
def test_el_admin_de_dominio_no_recibe_flags_de_django(admin_user):
    assert admin_user.is_staff is False
    assert admin_user.is_superuser is False


# --------------------------------------------------------------------------- #
# IsOperatorOfReportInstitution
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_operador_solo_accede_a_objetos_de_su_institucion(operator_user, institution):
    permiso = permissions.IsOperatorOfReportInstitution()
    otra = Institution.objects.get(slug="ande")
    peticion = _peticion(operator_user)

    assert permiso.has_permission(peticion, None) is True
    assert permiso.has_object_permission(peticion, None, _Objeto(institution.pk)) is True
    assert permiso.has_object_permission(peticion, None, _Objeto(otra.pk)) is False


@pytest.mark.django_db
def test_objeto_sin_institucion_se_deniega(operator_user):
    permiso = permissions.IsOperatorOfReportInstitution()
    peticion = _peticion(operator_user)

    assert permiso.has_object_permission(peticion, None, _Objeto(None)) is False
    assert permiso.has_object_permission(peticion, None, _SinInstitucion()) is False


@pytest.mark.django_db
def test_un_operador_puede_tener_varias_instituciones(operator_user, institution):
    """El modelo lo permite: ningún permiso asume una institución por operador."""
    otra = Institution.objects.get(slug="essap")
    InstitutionOperator.objects.create(user=operator_user, institution=otra, active=True)
    permiso = permissions.IsOperatorOfReportInstitution()
    peticion = _peticion(operator_user)

    assert permiso.has_object_permission(peticion, None, _Objeto(institution.pk)) is True
    assert permiso.has_object_permission(peticion, None, _Objeto(otra.pk)) is True


@pytest.mark.django_db
def test_admin_y_ciudadano_no_pasan_el_permiso_de_institucion(admin_user, citizen, institution):
    permiso = permissions.IsOperatorOfReportInstitution()

    for user in (admin_user, citizen):
        assert permiso.has_permission(_peticion(user), None) is False
        objeto = _Objeto(institution.pk)
        assert permiso.has_object_permission(_peticion(user), None, objeto) is False


# --------------------------------------------------------------------------- #
# Autenticación por JWT en la configuración
# --------------------------------------------------------------------------- #


def test_la_api_autentica_con_jwt(settings):
    assert settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] == [
        "rest_framework_simplejwt.authentication.JWTAuthentication"
    ]


def test_el_jwt_no_rota_ni_usa_blacklist(settings):
    assert settings.SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is False
    assert settings.SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] is False
    assert settings.SIMPLE_JWT["UPDATE_LAST_LOGIN"] is False
    assert "rest_framework_simplejwt.token_blacklist" not in settings.INSTALLED_APPS


@pytest.mark.django_db
def test_un_rol_manipulado_en_el_token_no_eleva_privilegios(citizen):
    """El rol se lee de la base de datos, nunca de un claim."""
    from rest_framework.test import APIClient

    refresh = RefreshToken.for_user(citizen)
    refresh["role"] = UserRole.ADMIN  # claim inyectado a mano
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    cuerpo = api.get("/api/v1/auth/me/").json()

    assert cuerpo["role"] == UserRole.CITIZEN
