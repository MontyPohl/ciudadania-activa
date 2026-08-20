"""Los tres endpoints de autenticación del Sprint 3."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.auth_app.enums import UserRole, VerificationStatus

pytestmark = pytest.mark.django_db

User = get_user_model()

GOOGLE_URL = "/api/v1/auth/google/"
REFRESH_URL = "/api/v1/auth/refresh/"
ME_URL = "/api/v1/auth/me/"

CAMPOS_ME = {
    "id",
    "email",
    "full_name",
    "role",
    "verification_status",
    "is_verified_citizen",
    "institutions",
}


@pytest.fixture
def api():
    return APIClient()


# --------------------------------------------------------------------------- #
# POST /auth/google/
# --------------------------------------------------------------------------- #


def test_login_emite_sesion_y_usuario(api, google_verifier, google_claims):
    google_verifier(claims=google_claims())

    respuesta = api.post(GOOGLE_URL, {"token_google": "id-token"})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"access", "refresh", "user"}
    assert cuerpo["access"] and cuerpo["refresh"]
    assert cuerpo["user"]["role"] == UserRole.CITIZEN
    assert cuerpo["user"]["verification_status"] == VerificationStatus.PENDIENTE
    assert cuerpo["user"]["is_verified_citizen"] is False
    assert cuerpo["user"]["institutions"] == []


def test_login_no_expone_campos_sensibles(api, google_verifier, google_claims):
    google_verifier(claims=google_claims())

    cuerpo = api.post(GOOGLE_URL, {"token_google": "id-token"}).json()

    assert set(cuerpo["user"]) == CAMPOS_ME
    for prohibido in ("password", "google_oauth_id", "is_staff", "is_superuser"):
        assert prohibido not in cuerpo["user"]


def test_login_sin_token_es_400(api, google_client_id):
    assert api.post(GOOGLE_URL, {}).status_code == 400
    assert api.post(GOOGLE_URL, {"token_google": ""}).status_code == 400


def test_login_con_token_invalido_es_401_generico(api, google_client_id, google_verifier):
    google_verifier(error=ValueError("Token expired"))

    respuesta = api.post(GOOGLE_URL, {"token_google": "id-token"})

    assert respuesta.status_code == 401
    detalle = respuesta.json()["detail"]
    for filtracion in ("audience", "expired", "signature", "issuer"):
        assert filtracion not in detalle.lower()


def test_login_con_email_no_verificado_es_401(api, google_verifier, google_claims):
    google_verifier(claims=google_claims(email_verified=False))

    assert api.post(GOOGLE_URL, {"token_google": "id-token"}).status_code == 401


def test_login_de_cuenta_inactiva_es_401_generico(
    api, user_factory, google_verifier, google_claims
):
    user_factory(email="inactivo@example.com", google_oauth_id="sub-inactivo", is_active=False)
    google_verifier(claims=google_claims(sub="sub-inactivo", email="inactivo@example.com"))

    respuesta = api.post(GOOGLE_URL, {"token_google": "id-token"})

    assert respuesta.status_code == 401
    assert "desactiv" not in respuesta.json()["detail"].lower()


def test_login_con_email_de_otra_identidad_es_409(
    api, user_factory, google_verifier, google_claims
):
    user_factory(email="ocupado@example.com", google_oauth_id="otro-sub")
    google_verifier(claims=google_claims(email="ocupado@example.com"))

    respuesta = api.post(GOOGLE_URL, {"token_google": "id-token"})

    assert respuesta.status_code == 409


def test_login_con_email_externo_sobre_cuenta_existente_es_409(
    api, user_factory, google_verifier, google_claims
):
    """Caso C con email externo: no se auto-vincula, y el motivo no se filtra."""
    user = user_factory(email="operador@example.com")
    google_verifier(claims=google_claims(email="operador@example.com"))

    respuesta = api.post(GOOGLE_URL, {"token_google": "id-token"})

    assert respuesta.status_code == 409
    detalle = respuesta.json()["detail"].lower()
    for filtracion in ("gmail", "workspace", "externo", "hd", "dominio"):
        assert filtracion not in detalle

    user.refresh_from_db()
    assert user.google_oauth_id is None


def test_login_con_gmail_sobre_cuenta_existente_vincula(
    api, user_factory, google_verifier, google_claims
):
    user = user_factory(email="operador@gmail.com")
    google_verifier(claims=google_claims(email="operador@gmail.com"))

    respuesta = api.post(GOOGLE_URL, {"token_google": "id-token"})

    assert respuesta.status_code == 200
    user.refresh_from_db()
    assert user.google_oauth_id == google_claims()["sub"]


def test_login_con_google_inalcanzable_es_503(api, google_client_id, google_verifier):
    from google.auth.exceptions import TransportError

    google_verifier(error=TransportError("connection failed"))

    assert api.post(GOOGLE_URL, {"token_google": "id-token"}).status_code == 503


def test_el_cliente_no_puede_elegir_su_rol(api, google_verifier, google_claims):
    """El rol y la verificación los decide el servidor, nunca el cuerpo de la petición."""
    google_verifier(claims=google_claims())

    cuerpo = api.post(
        GOOGLE_URL,
        {
            "token_google": "id-token",
            "role": UserRole.ADMIN,
            "verification_status": VerificationStatus.APROBADO,
            "email": "atacante@example.com",
            "is_staff": True,
        },
    ).json()

    creado = User.objects.get(pk=cuerpo["user"]["id"])
    assert creado.role == UserRole.CITIZEN
    assert creado.verification_status == VerificationStatus.PENDIENTE
    assert creado.email == "nuevo@example.com"
    assert creado.is_staff is False


def test_el_access_emitido_sirve_para_autenticar(api, google_verifier, google_claims):
    google_verifier(claims=google_claims())
    access = api.post(GOOGLE_URL, {"token_google": "id-token"}).json()["access"]

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    assert api.get(ME_URL).status_code == 200


# --------------------------------------------------------------------------- #
# POST /auth/refresh/
# --------------------------------------------------------------------------- #


def test_refresh_devuelve_solo_un_access_nuevo(api, citizen):
    refresh = str(RefreshToken.for_user(citizen))

    respuesta = api.post(REFRESH_URL, {"refresh": refresh})

    assert respuesta.status_code == 200
    assert set(respuesta.json()) == {"access"}


def test_refresh_no_rota_el_token(api, citizen):
    """Sin blacklist, rotar no revocaría el anterior: el mismo refresh sigue sirviendo."""
    refresh = str(RefreshToken.for_user(citizen))

    primero = api.post(REFRESH_URL, {"refresh": refresh})
    segundo = api.post(REFRESH_URL, {"refresh": refresh})

    assert primero.status_code == segundo.status_code == 200
    assert "refresh" not in primero.json()


def test_refresh_sin_cuerpo_es_400(api):
    assert api.post(REFRESH_URL, {}).status_code == 400


@pytest.mark.parametrize("valor", ["no-es-un-jwt", "a.b.c"], ids=["basura", "jwt-falso"])
def test_refresh_invalido_es_401(api, valor):
    assert api.post(REFRESH_URL, {"refresh": valor}).status_code == 401


def test_refresh_de_usuario_inactivo_es_401(api, user_factory):
    """SimpleJWT por sí solo no consulta `is_active` al renovar; la vista propia sí."""
    user = user_factory(email="desactivado@example.com")
    refresh = str(RefreshToken.for_user(user))
    user.is_active = False
    user.save(update_fields=["is_active"])

    respuesta = api.post(REFRESH_URL, {"refresh": refresh})

    assert respuesta.status_code == 401


def test_refresh_de_usuario_borrado_es_401(api, user_factory):
    user = user_factory(email="borrado@example.com")
    refresh = str(RefreshToken.for_user(user))
    user.delete()

    assert api.post(REFRESH_URL, {"refresh": refresh}).status_code == 401


def test_refresh_con_user_id_no_uuid_es_401(api, citizen):
    """Un claim `user_id` manipulado a un valor que no es UUID no debe romper la vista."""
    refresh = RefreshToken.for_user(citizen)
    refresh["user_id"] = "no-es-un-uuid"

    assert api.post(REFRESH_URL, {"refresh": str(refresh)}).status_code == 401


def test_refresh_sin_claim_de_usuario_es_401(api, citizen):
    refresh = RefreshToken.for_user(citizen)
    del refresh.payload["user_id"]

    assert api.post(REFRESH_URL, {"refresh": str(refresh)}).status_code == 401


def test_refresh_no_actualiza_last_login(api, citizen):
    refresh = str(RefreshToken.for_user(citizen))
    citizen.refresh_from_db()
    antes = citizen.last_login

    api.post(REFRESH_URL, {"refresh": refresh})
    citizen.refresh_from_db()

    assert citizen.last_login == antes


def test_el_refresh_no_sirve_como_access(api, citizen):
    """Un refresh presentado como Bearer no debe autenticar."""
    refresh = str(RefreshToken.for_user(citizen))
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh}")

    assert api.get(ME_URL).status_code == 401


# --------------------------------------------------------------------------- #
# GET /auth/me/
# --------------------------------------------------------------------------- #


def test_me_anonimo_es_401(api):
    assert api.get(ME_URL).status_code == 401


def test_me_devuelve_el_perfil_seguro(api, citizen):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(citizen).access_token}")

    cuerpo = api.get(ME_URL).json()

    assert set(cuerpo) == CAMPOS_ME
    assert cuerpo["email"] == citizen.email
    assert cuerpo["institutions"] == []


def test_me_de_operador_incluye_sus_instituciones(api, operator_user, institution):
    api.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(operator_user).access_token}"
    )

    cuerpo = api.get(ME_URL).json()

    assert cuerpo["role"] == UserRole.OPERATOR
    assert len(cuerpo["institutions"]) == 1
    assert cuerpo["institutions"][0]["slug"] == institution.slug
    assert set(cuerpo["institutions"][0]) == {"id", "name", "slug"}


def test_me_de_operador_sin_vinculo_activo_no_lista_instituciones(api, operator_user, institution):
    from apps.admin_app.models import InstitutionOperator

    InstitutionOperator.objects.filter(user=operator_user).update(active=False)
    api.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(operator_user).access_token}"
    )

    assert api.get(ME_URL).json()["institutions"] == []


def test_me_de_ciudadano_verificado_expone_el_derivado(api, user_factory):
    user = user_factory(
        email="verificado@example.com", verification_status=VerificationStatus.APROBADO
    )
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")

    assert api.get(ME_URL).json()["is_verified_citizen"] is True


def test_me_de_usuario_inactivo_es_401(api, user_factory):
    user = user_factory(email="inactivo2@example.com")
    access = str(RefreshToken.for_user(user).access_token)
    user.is_active = False
    user.save(update_fields=["is_active"])

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    assert api.get(ME_URL).status_code == 401


def test_no_existe_endpoint_de_logout_en_django(api):
    """El cierre de sesión lo resuelve el BFF borrando la cookie (ADR-0005)."""
    respuesta = api.post("/api/v1/auth/logout/", {})

    assert respuesta.status_code == 404


def test_el_token_usa_la_pk_uuid_del_usuario(citizen):
    refresh = RefreshToken.for_user(citizen)

    assert refresh.payload["user_id"] == str(citizen.pk)


def test_reverse_de_las_rutas_de_auth():
    assert reverse("auth-google") == GOOGLE_URL
    assert reverse("auth-refresh") == REFRESH_URL
    assert reverse("auth-me") == ME_URL
