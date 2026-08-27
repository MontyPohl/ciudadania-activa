from decimal import Decimal
from itertools import combinations

import pytest

from apps.map_app.bbox import BoundingBox, InvalidBoundingBox, parse_and_canonicalize_bbox


def test_bbox_expande_a_fronteras_half_step_y_no_a_multiplos_simples():
    bbox = parse_and_canonicalize_bbox("-57.63595,-25.26395,-57.63575,-25.26375")

    assert bbox == BoundingBox(
        west=Decimal("-57.6365"),
        south=Decimal("-25.2645"),
        east=Decimal("-57.6355"),
        north=Decimal("-25.2635"),
    )
    assert bbox.as_cache_value() == "-57.6365,-25.2645,-57.6355,-25.2635"


def test_bbox_equivalentes_dentro_de_una_celda_tienen_la_misma_forma_canonica():
    first = parse_and_canonicalize_bbox("-57.63595,-25.26395,-57.63575,-25.26375")
    second = parse_and_canonicalize_bbox("-57.63585,-25.26385,-57.63555,-25.26355")

    assert first == second


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "1,2,3",
        "1,2,3,4,5",
        "1,,3,4",
        "texto,2,3,4",
        "NaN,0,1,2",
        "-Infinity,0,1,2",
        "0,0,Infinity,2",
        "-181,0,-180,1",
        "0,-91,1,-90",
        "180,0,181,1",
        "0,90,1,91",
        "1,0,1,1",
        "2,0,1,1",
        "0,1,1,1",
        "0,2,1,1",
        "170,-1,-170,1",
        "0,0,0.500001,0.5",
        "0,0,0.5,0.500001",
    ],
)
def test_bbox_rechaza_formas_invalidas(raw):
    with pytest.raises(InvalidBoundingBox):
        parse_and_canonicalize_bbox(raw)


def test_bbox_acepta_exactamente_medio_grado_por_eje():
    bbox = parse_and_canonicalize_bbox("-58,-25.5,-57.5,-25")

    # La consulta se expande como máximo medio paso por cada lado después de validar
    # el límite solicitado, de modo que nunca corta una celda pública.
    assert bbox.width == Decimal("0.501")
    assert bbox.height == Decimal("0.501")
    emitted = bbox.as_public_query_value()
    emitted_values = tuple(Decimal(value) for value in emitted.split(","))
    assert emitted_values[2] - emitted_values[0] == Decimal("0.5")
    assert emitted_values[3] - emitted_values[1] == Decimal("0.5")
    assert parse_and_canonicalize_bbox(emitted) == bbox


def test_envelope_conserva_srid_4326():
    envelope = parse_and_canonicalize_bbox("-57.7,-25.35,-57.5,-25.2").envelope()

    assert envelope.srid == 4326


def test_expansion_no_sale_de_los_limites_globales():
    southwest = parse_and_canonicalize_bbox("-180,-90,-179.9,-89.9")
    northeast = parse_and_canonicalize_bbox("179.9,89.9,180,90")

    assert southwest.west == Decimal("-180")
    assert southwest.south == Decimal("-90")
    assert northeast.east == Decimal("180")
    assert northeast.north == Decimal("90")
    assert southwest.envelope().extent[:2] == (-180.0, -90.0)
    assert northeast.envelope().extent[2:] == (180.0, 90.0)


@pytest.mark.parametrize("axis", ["longitude", "latitude"])
@pytest.mark.parametrize(
    "public_value",
    [
        Decimal("-57.636"),
        Decimal("-25.264"),
        Decimal("-0.001"),
        Decimal("0.000"),
        Decimal("0.001"),
        Decimal("25.264"),
        Decimal("57.636"),
    ],
)
def test_ningun_bbox_canonico_separa_puntos_de_la_misma_celda(public_value, axis):
    half = Decimal("0.0005")
    epsilon = Decimal("0.0000001")
    candidates = (
        public_value - half - epsilon,
        public_value - half,
        public_value - half + epsilon,
        public_value,
        public_value + half - epsilon,
        public_value + half,
        public_value + half + epsilon,
    )
    if public_value < 0:
        same_cell = (public_value - half + epsilon, public_value, public_value + half)
    elif public_value > 0:
        same_cell = (public_value - half, public_value, public_value + half - epsilon)
    else:
        same_cell = (-half + epsilon, Decimal("0"), half - epsilon)

    for lower, upper in combinations(candidates, 2):
        if axis == "longitude":
            raw = f"{lower},-0.1,{upper},0.1"
            bounds = (0, 2)
        else:
            raw = f"-0.1,{lower},0.1,{upper}"
            bounds = (1, 3)
        extent = parse_and_canonicalize_bbox(raw).envelope().extent
        memberships = {
            extent[bounds[0]] <= float(value) <= extent[bounds[1]] for value in same_cell
        }
        assert len(memberships) == 1, (axis, public_value, raw, same_cell, extent)
