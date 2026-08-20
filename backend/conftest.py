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
def report(citizen, category):
    return Report.objects.create(
        citizen=citizen,
        category=category,
        description="Bache profundo en la calzada.",
        location=Point(*ASUNCION, srid=4326),
    )
