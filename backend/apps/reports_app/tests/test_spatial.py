"""Verificaciones geoespaciales del Sprint 2.

Comprueban que la columna es `geometry(Point,4326)`, que el índice GIST existe
realmente en PostgreSQL y que `ST_DWithin` responde. Todas las funciones usadas
existen en PostGIS 3.3.7, el piso de compatibilidad productivo (ADR-0003).
"""

import pytest
from django.contrib.gis.geos import Point
from django.db import connection

from apps.reports_app.models import Report

pytestmark = pytest.mark.django_db

# Microcentro de Asunción y un punto a ~35 m de distancia.
CENTRO = Point(-57.6359, -25.2637, srid=4326)
CERCA = Point(-57.63555, -25.26375, srid=4326)
LEJOS = Point(-57.5759, -25.3037, srid=4326)


def test_la_columna_es_geometry_point_4326():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT type, srid, coord_dimension
            FROM geometry_columns
            WHERE f_table_name = 'reports' AND f_geometry_column = 'location'
            """
        )
        tipo, srid, dimension = cursor.fetchone()

    assert (tipo, srid, dimension) == ("POINT", 4326, 2)


def test_existe_el_indice_gist_sobre_location():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'reports' AND indexdef ILIKE '%%USING gist%%'
            """
        )
        definiciones = [fila[0] for fila in cursor.fetchall()]

    assert len(definiciones) == 1
    assert "location" in definiciones[0]


def test_el_punto_hace_ida_y_vuelta_conservando_el_srid(citizen, category):
    reporte = Report.objects.create(
        citizen=citizen, category=category, description="d", location=CENTRO
    )
    reporte.refresh_from_db()

    assert reporte.location.srid == 4326
    assert reporte.location.x == pytest.approx(CENTRO.x)
    assert reporte.location.y == pytest.approx(CENTRO.y)


def test_st_dwithin_en_metros_mediante_cast_a_geography(citizen, category):
    """Anticipa la consulta de duplicados del Sprint 6 (100 m, CT-18).

    La columna es `geometry` (D2-07), así que el radio en metros se resuelve con
    un cast a `geography` en la consulta, no cambiando el tipo de la columna.
    """
    cercano = Report.objects.create(
        citizen=citizen, category=category, description="cercano", location=CERCA
    )
    Report.objects.create(citizen=citizen, category=category, description="lejano", location=LEJOS)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id FROM reports
            WHERE ST_DWithin(
                location::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                100
            )
            """,
            [CENTRO.x, CENTRO.y],
        )
        encontrados = [fila[0] for fila in cursor.fetchall()]

    assert encontrados == [cercano.pk]


def test_st_distance_sphere_mide_en_metros():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ST_DistanceSphere(
                ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            )
            """,
            [CENTRO.x, CENTRO.y, CERCA.x, CERCA.y],
        )
        (metros,) = cursor.fetchone()

    assert 0 < metros < 100
