"""Verificación del ID token de Google (ADR-0005).

Responsabilidad única: recibir el *credential* que Google Identity Services entrega
al navegador y devolver **claims normalizados y confiables**. No crea usuarios, no
emite JWT propios, no conoce HTTP y no decide roles.

El backend nunca confía en el email, el nombre ni ningún otro dato que el navegador
envíe por su cuenta: todo lo que se usa proviene de un token verificado
criptográficamente contra las claves públicas de Google.

Ni el token ni el `sub` se registran en logs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)


class GoogleIdentityError(Exception):
    """El token no acredita una identidad utilizable. Se traduce a 401 genérico."""


class GoogleUnavailableError(Exception):
    """No se pudo verificar el token por causa del servidor. Se traduce a 503."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Claims verificados que el resto del dominio puede usar con confianza."""

    sub: str
    email: str
    full_name: str
    #: Claim `hd`: dominio de Google Workspace al que pertenece la cuenta. Cadena vacía
    #: si el token no lo trae. **No es una lista blanca**: el login no se restringe a
    #: ningún dominio. Solo sirve como prueba de autoridad en el caso C del linking.
    hosted_domain: str = ""

    @property
    def google_is_authoritative_email(self) -> bool:
        """¿Google es autoridad sobre la **propiedad actual** de esta dirección?

        Lo es en dos supuestos:

        - la dirección es `@gmail.com`, es decir Google la administra;
        - la cuenta pertenece a un dominio de Google Workspace (claim `hd` presente).

        En una cuenta de Google creada con un email **externo**, `email_verified` puede
        ser `true` porque Google comprobó la dirección al darla de alta, pero eso no
        garantiza que la persona siga controlándola hoy. Por eso ese caso no basta para
        apropiarse de una cuenta local preexistente (caso C del linking).

        `email_verified` no se vuelve a comprobar aquí: `verify_id_token` ya rechaza
        cualquier token que no lo traiga en `true`, así que es una precondición del
        contrato de esta clase.
        """
        return self.email.endswith("@gmail.com") or bool(self.hosted_domain)


def _require_client_id() -> str:
    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    if not client_id:
        # Sin `audience` no se puede validar para quién se emitió el token, así que
        # verificar sería inseguro. Se falla de forma cerrada, nunca permisiva.
        logger.error("GOOGLE_OAUTH_CLIENT_ID no está configurado: login deshabilitado.")
        raise GoogleUnavailableError("La autenticación con Google no está configurada.")
    return client_id


def verify_id_token(raw_token: str) -> GoogleIdentity:
    """Verifica un ID token de Google y devuelve su identidad.

    Comprueba, mediante `google-auth`: firma RS256 contra el JWKS de Google, emisor
    (`iss`), destinatario (`aud` == `GOOGLE_OAUTH_CLIENT_ID`) y expiración (`exp`).
    Después exige explícitamente `sub`, `email` y `email_verified is True`.

    Levanta `GoogleIdentityError` si el token no sirve y `GoogleUnavailableError` si
    el fallo es del servidor o de la configuración.
    """
    client_id = _require_client_id()

    if not raw_token:
        raise GoogleIdentityError("Token de Google ausente.")

    try:
        claims = google_id_token.verify_oauth2_token(
            raw_token,
            google_requests.Request(),
            audience=client_id,
        )
    except TransportError as exc:
        # Fallo de red al obtener las claves públicas: no es culpa del cliente y no
        # debe degradarse a "token inválido".
        logger.warning(
            "No se pudieron obtener las claves públicas de Google: %s", type(exc).__name__
        )
        raise GoogleUnavailableError("No se pudo verificar la identidad con Google.") from exc
    except (GoogleAuthError, ValueError) as exc:
        # Cubre firma inválida, `aud` incorrecta, `iss` incorrecto, token expirado y
        # token malformado. El motivo exacto no se expone al cliente.
        logger.info("ID token de Google rechazado: %s", type(exc).__name__)
        raise GoogleIdentityError("El token de Google no es válido.") from exc

    return _identity_from_claims(claims)


def _identity_from_claims(claims: dict) -> GoogleIdentity:
    sub = (claims.get("sub") or "").strip()
    if not sub:
        raise GoogleIdentityError("El token de Google no incluye el identificador de la cuenta.")

    email = (claims.get("email") or "").strip()
    if not email:
        raise GoogleIdentityError("El token de Google no incluye un email.")

    # `email_verified` puede llegar como booleano o como cadena según el emisor.
    email_verified = claims.get("email_verified")
    if email_verified not in (True, "true", "True"):
        raise GoogleIdentityError("La cuenta de Google no tiene el email verificado.")

    return GoogleIdentity(
        sub=sub,
        email=email.lower(),
        full_name=(claims.get("name") or "").strip(),
        hosted_domain=(claims.get("hd") or "").strip().lower(),
    )
