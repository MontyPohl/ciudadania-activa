"""Fixtures compartidas de la suite del backend.

Se limitan a construir instancias mínimas válidas de los modelos del Sprint 2:
ninguna reproduce reglas de negocio de sprints futuros.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point

from apps.admin_app.models import Category, Institution
from apps.reports_app.models import Report

# Microcentro de Asunción, en el orden (lon, lat) que espera PostGIS.
ASUNCION = (-57.6359, -25.2637)


@pytest.fixture
def user_factory(db):
    def make_user(email="ciudadano@example.com", **extra):
        return get_user_model().objects.create_user(
            email=email, password="clave-segura-123", **extra
        )

    return make_user


@pytest.fixture
def citizen(user_factory):
    return user_factory(email="autor@example.com")


@pytest.fixture
def other_citizen(user_factory):
    return user_factory(email="confirmador@example.com")


@pytest.fixture
def institution(db):
    return Institution.objects.get(slug="municipalidad-de-asuncion")


@pytest.fixture
def category(db):
    return Category.objects.get(slug="baches")


@pytest.fixture
def operator_user(user_factory, institution):
    """Operador con vínculo institucional activo."""
    from apps.admin_app.models import InstitutionOperator
    from apps.auth_app.enums import UserRole

    user = user_factory(email="operador@example.com", role=UserRole.OPERATOR)
    InstitutionOperator.objects.create(user=user, institution=institution, active=True)
    return user


@pytest.fixture
def admin_user(user_factory):
    """Administrador de dominio. Deliberadamente sin `is_staff` ni `is_superuser`."""
    from apps.auth_app.enums import UserRole

    return user_factory(email="admin@example.com", role=UserRole.ADMIN)


@pytest.fixture
def google_client_id(settings):
    """Configura el `audience` que el verificador debe exigir."""
    settings.GOOGLE_OAUTH_CLIENT_ID = "cliente-de-prueba.apps.googleusercontent.com"
    return settings.GOOGLE_OAUTH_CLIENT_ID


@pytest.fixture
def google_claims(google_client_id):
    """Fábrica de claims verificados, tal como los devolvería `google-auth`."""

    def make(**overrides):
        claims = {
            "iss": "https://accounts.google.com",
            "aud": google_client_id,
            "sub": "115000000000000000001",
            "email": "nuevo@example.com",
            "email_verified": True,
            "name": "Nombre Apellido",
        }
        claims.update(overrides)
        return claims

    return make


@pytest.fixture
def google_verifier(monkeypatch):
    """Sustituye la verificación real por un doble controlado.

    Ninguna prueba sale a la red: se reemplaza `verify_oauth2_token`, de modo que la
    validación de claims propia del servicio sí se ejercita.
    """

    def install(claims=None, error=None):
        def fake_verify(token, request, audience=None, **kwargs):
            if error is not None:
                raise error
            return claims

        monkeypatch.setattr(
            "apps.auth_app.services.google.google_id_token.verify_oauth2_token",
            fake_verify,
        )

    return install


@pytest.fixture
def report(citizen, category):
    return Report.objects.create(
        citizen=citizen,
        category=category,
        description="Bache profundo en la calzada.",
        location=Point(*ASUNCION, srid=4326),
    )
