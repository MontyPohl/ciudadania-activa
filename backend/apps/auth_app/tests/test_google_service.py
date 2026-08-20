"""Verificación del ID token de Google: casos válidos y todos los rechazos."""

import pytest
from google.auth.exceptions import GoogleAuthError, TransportError

from apps.auth_app.services import google as google_service


def test_token_valido_devuelve_identidad_normalizada(google_verifier, google_claims):
    google_verifier(claims=google_claims(email="Persona@Example.com", name="  Ana Díaz  "))

    identidad = google_service.verify_id_token("id-token")

    assert identidad.sub == "115000000000000000001"
    assert identidad.email == "persona@example.com"  # normalizado a minúsculas
    assert identidad.full_name == "Ana Díaz"


def test_token_vacio_se_rechaza(google_client_id):
    with pytest.raises(google_service.GoogleIdentityError):
        google_service.verify_id_token("")


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Token expired"),
        ValueError("Token has wrong audience"),
        ValueError("Could not verify token signature"),
        GoogleAuthError("Wrong issuer"),
    ],
    ids=["expirado", "audience-incorrecta", "firma-invalida", "issuer-incorrecto"],
)
def test_errores_de_verificacion_se_traducen_a_identidad_invalida(
    google_client_id, google_verifier, error
):
    google_verifier(error=error)

    with pytest.raises(google_service.GoogleIdentityError):
        google_service.verify_id_token("id-token")


def test_fallo_de_red_no_se_confunde_con_token_invalido(google_client_id, google_verifier):
    """Un JWKS inalcanzable es culpa del servidor, no del cliente."""
    google_verifier(error=TransportError("connection failed"))

    with pytest.raises(google_service.GoogleUnavailableError):
        google_service.verify_id_token("id-token")


def test_sin_client_id_configurado_no_se_verifica_nada(settings, google_verifier, google_claims):
    """Sin `audience` la verificación sería insegura: se falla cerrado."""
    google_verifier(claims=google_claims())
    settings.GOOGLE_OAUTH_CLIENT_ID = ""

    with pytest.raises(google_service.GoogleUnavailableError):
        google_service.verify_id_token("id-token")


def test_email_no_verificado_se_rechaza(google_verifier, google_claims):
    google_verifier(claims=google_claims(email_verified=False))

    with pytest.raises(google_service.GoogleIdentityError):
        google_service.verify_id_token("id-token")


@pytest.mark.parametrize("valor", [None, "", "   "], ids=["ausente", "vacio", "espacios"])
def test_sub_obligatorio(google_verifier, google_claims, valor):
    google_verifier(claims=google_claims(sub=valor))

    with pytest.raises(google_service.GoogleIdentityError):
        google_service.verify_id_token("id-token")


@pytest.mark.parametrize("valor", [None, ""], ids=["ausente", "vacio"])
def test_email_obligatorio(google_verifier, google_claims, valor):
    google_verifier(claims=google_claims(email=valor))

    with pytest.raises(google_service.GoogleIdentityError):
        google_service.verify_id_token("id-token")


def test_email_verified_como_cadena_tambien_se_acepta(google_verifier, google_claims):
    google_verifier(claims=google_claims(email_verified="true"))

    assert google_service.verify_id_token("id-token").email == "nuevo@example.com"


def test_el_audience_configurado_se_pasa_al_verificador(
    monkeypatch, google_client_id, google_claims
):
    recibido = {}

    def fake_verify(token, request, audience=None, **kwargs):
        recibido["audience"] = audience
        return google_claims()

    monkeypatch.setattr(
        "apps.auth_app.services.google.google_id_token.verify_oauth2_token", fake_verify
    )

    google_service.verify_id_token("id-token")

    assert recibido["audience"] == google_client_id


def test_el_claim_hd_se_normaliza(google_verifier, google_claims):
    google_verifier(claims=google_claims(hd="  Empresa.COM  "))

    assert google_service.verify_id_token("id-token").hosted_domain == "empresa.com"


def test_sin_claim_hd_el_dominio_queda_vacio(google_verifier, google_claims):
    google_verifier(claims=google_claims())

    assert google_service.verify_id_token("id-token").hosted_domain == ""


@pytest.mark.parametrize(
    ("email", "hd", "autoritativo"),
    [
        ("persona@gmail.com", "", True),
        ("persona@empresa.com", "empresa.com", True),
        ("persona@example.com", "", False),
        ("persona@gmail.com.attacker.test", "", False),
    ],
    ids=["gmail", "workspace", "email-externo", "dominio-parecido-a-gmail"],
)
def test_regla_de_autoridad_de_google_sobre_el_email(
    google_verifier, google_claims, email, hd, autoritativo
):
    """Solo `@gmail.com` o un dominio de Workspace acreditan autoridad actual."""
    google_verifier(claims=google_claims(email=email, hd=hd))

    identidad = google_service.verify_id_token("id-token")

    assert identidad.google_is_authoritative_email is autoritativo
