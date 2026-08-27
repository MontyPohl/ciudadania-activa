import json
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point

from apps.map_app.serializers import PublicReportSerializer, public_coordinate_decimal

pytestmark = pytest.mark.django_db


def test_serializer_publico_tiene_lista_blanca_exacta_y_redondea(public_report_factory):
    report = public_report_factory(
        location=Point(-57.6355, -25.2635, srid=4326),
        description="Texto sintético.",
    )

    data = PublicReportSerializer(report).data

    assert set(data) == {
        "id",
        "category",
        "institution",
        "description",
        "status",
        "priority",
        "latitude",
        "longitude",
        "created_at",
    }
    assert set(data["category"]) == {"id", "name", "slug"}
    assert set(data["institution"]) == {"id", "name", "slug"}
    assert data["latitude"] == -25.264
    assert data["longitude"] == -57.636
    assert isinstance(data["latitude"], float)
    assert isinstance(data["longitude"], float)

    serialized = json.dumps(data)
    for private_name in (
        "citizen",
        "email",
        "actor",
        "validated_by",
        "rejection_reason",
        "source",
        "location",
        "updated_at",
        "validated_at",
        "resolved_at",
        "media",
        "history",
        "confirmations",
    ):
        assert private_name not in serialized


def test_serializer_no_sustituye_institucion_nula(public_report_factory):
    report = public_report_factory(institution=None)

    assert PublicReportSerializer(report).data["institution"] is None


@pytest.mark.parametrize(
    ("exact", "public"),
    [
        ("-57.6365000", "-57.637"),
        ("-57.6364999", "-57.636"),
        ("-57.6355001", "-57.636"),
        ("-57.6355000", "-57.636"),
        ("-57.6354999", "-57.635"),
        ("-25.2645000", "-25.265"),
        ("-25.2644999", "-25.264"),
        ("-25.2635000", "-25.264"),
        ("-25.2634999", "-25.263"),
        ("-0.0005000", "-0.001"),
        ("-0.0004999", "0.000"),
        ("0", "0.000"),
        ("0.0004999", "0.000"),
        ("0.0005000", "0.001"),
        ("25.2634999", "25.263"),
        ("25.2635000", "25.264"),
        ("25.2644999", "25.264"),
        ("25.2645000", "25.265"),
        ("57.6354999", "57.635"),
        ("57.6355000", "57.636"),
        ("57.6364999", "57.636"),
        ("57.6365000", "57.637"),
    ],
)
def test_tabla_de_verdad_round_half_up_en_half_steps(exact, public):
    assert public_coordinate_decimal(exact) == Decimal(public)


def test_conversion_geos_usa_decimal_de_su_texto_y_no_decimal_float():
    geos_double = float("-57.6365")

    assert public_coordinate_decimal(geos_double) == Decimal("-57.637")
    assert Decimal(geos_double) != Decimal(str(geos_double))
