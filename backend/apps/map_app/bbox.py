"""Validación y canonización de la ventana geográfica del mapa público.

Las coordenadas publicadas se redondean a ``0.001°`` con ``ROUND_HALF_UP``. Por eso
las fronteras de sus celdas de privacidad no son múltiplos de ``0.001``, sino la
cuadrícula desplazada ``0.0005°``. Toda consulta se expande a esa cuadrícula antes de
llegar a PostGIS: variar límites dentro de una celda no permite sondear la ubicación
exacta de un punto que se representa con la misma coordenada pública.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from math import inf, nextafter

from django.conf import settings
from django.contrib.gis.geos import Polygon

PUBLIC_COORDINATE_QUANTUM = Decimal("0.001")
PUBLIC_CELL_HALF_STEP = Decimal("0.0005")


class InvalidBoundingBox(ValueError):
    """El parámetro público ``bbox`` no satisface su contrato."""


@dataclass(frozen=True, slots=True)
class BoundingBox:
    west: Decimal
    south: Decimal
    east: Decimal
    north: Decimal

    @property
    def width(self) -> Decimal:
        return self.east - self.west

    @property
    def height(self) -> Decimal:
        return self.north - self.south

    def canonical(self) -> "BoundingBox":
        """Expande hacia afuera hasta fronteras ``n * 0.001 + 0.0005``."""
        return BoundingBox(
            west=max(Decimal("-180"), _cell_boundary_floor(self.west)),
            south=max(Decimal("-90"), _cell_boundary_floor(self.south)),
            east=min(Decimal("180"), _cell_boundary_ceil(self.east)),
            north=min(Decimal("90"), _cell_boundary_ceil(self.north)),
        )

    def as_cache_value(self) -> str:
        """Representación decimal estable; nunca usa notación exponencial."""
        return ",".join(_plain_decimal(value) for value in self.as_tuple())

    def as_public_query_value(self) -> str:
        """Bbox reproducible y válido para los enlaces de paginación.

        La forma canónica puede medir hasta ``0.501°`` porque expande ambos extremos
        medio paso. Si supera el máximo de entrada, se eligen puntos interiores de las
        mismas celdas limítrofes. Al volver a validarlos producen exactamente este bbox
        canónico, pero el parámetro emitido nunca supera ``0.5°``.
        """
        west, east = _fit_axis_for_public_query(
            self.west,
            self.east,
            world_min=Decimal("-180"),
            world_max=Decimal("180"),
        )
        south, north = _fit_axis_for_public_query(
            self.south,
            self.north,
            world_min=Decimal("-90"),
            world_max=Decimal("90"),
        )
        return ",".join(_plain_decimal(value) for value in (west, south, east, north))

    def as_tuple(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        return self.west, self.south, self.east, self.north

    def envelope(self) -> Polygon:
        """Rectángulo cerrado que respeta las clases exactas de ``ROUND_HALF_UP``.

        PostGIS ``&&`` incluye ambos bordes. En coordenadas negativas, el empate del
        borde inferior pertenece a la celda izquierda; en positivas, el empate del
        borde superior pertenece a la derecha. ``nextafter`` convierte esos dos bordes
        lógicamente abiertos en el siguiente ``double`` representable, que es también
        el tipo de coordenada almacenado por GEOS/PostGIS. Los límites mundiales son
        siempre inclusivos.
        """
        west = _lower_envelope_bound(
            self.west,
            world_min=Decimal("-180"),
        )
        south = _lower_envelope_bound(
            self.south,
            world_min=Decimal("-90"),
        )
        east = _upper_envelope_bound(
            self.east,
            world_max=Decimal("180"),
        )
        north = _upper_envelope_bound(
            self.north,
            world_max=Decimal("90"),
        )
        polygon = Polygon.from_bbox((west, south, east, north))
        polygon.srid = 4326
        return polygon


def parse_and_canonicalize_bbox(raw_value: object) -> BoundingBox:
    """Valida ``west,south,east,north`` y devuelve su expansión de privacidad."""
    if not isinstance(raw_value, str):
        raise InvalidBoundingBox("Usá el formato west,south,east,north.")

    components = raw_value.split(",")
    if len(components) != 4 or any(not component.strip() for component in components):
        raise InvalidBoundingBox("El bbox debe contener exactamente cuatro números.")

    try:
        values = tuple(Decimal(component.strip()) for component in components)
    except InvalidOperation as exc:
        raise InvalidBoundingBox("Todos los componentes del bbox deben ser numéricos.") from exc

    if not all(value.is_finite() for value in values):
        raise InvalidBoundingBox("Los componentes del bbox deben ser números finitos.")

    bbox = BoundingBox(*values)
    _validate_ranges(bbox)

    if bbox.west >= bbox.east:
        raise InvalidBoundingBox("west debe ser menor que east; no se admite antimeridiano.")
    if bbox.south >= bbox.north:
        raise InvalidBoundingBox("south debe ser menor que north.")

    maximum = settings.PUBLIC_MAP_MAX_BBOX_SPAN_DEGREES
    if bbox.width > maximum or bbox.height > maximum:
        raise InvalidBoundingBox("El ancho y el alto del bbox no pueden superar 0.5 grados.")

    return bbox.canonical()


def _validate_ranges(bbox: BoundingBox) -> None:
    if not Decimal("-180") <= bbox.west <= Decimal("180"):
        raise InvalidBoundingBox("west debe estar entre -180 y 180.")
    if not Decimal("-180") <= bbox.east <= Decimal("180"):
        raise InvalidBoundingBox("east debe estar entre -180 y 180.")
    if not Decimal("-90") <= bbox.south <= Decimal("90"):
        raise InvalidBoundingBox("south debe estar entre -90 y 90.")
    if not Decimal("-90") <= bbox.north <= Decimal("90"):
        raise InvalidBoundingBox("north debe estar entre -90 y 90.")


def _cell_boundary_floor(value: Decimal) -> Decimal:
    units = (value - PUBLIC_CELL_HALF_STEP) / PUBLIC_COORDINATE_QUANTUM
    return (
        units.to_integral_value(rounding=ROUND_FLOOR) * PUBLIC_COORDINATE_QUANTUM
        + PUBLIC_CELL_HALF_STEP
    )


def _cell_boundary_ceil(value: Decimal) -> Decimal:
    units = (value - PUBLIC_CELL_HALF_STEP) / PUBLIC_COORDINATE_QUANTUM
    return (
        units.to_integral_value(rounding=ROUND_CEILING) * PUBLIC_COORDINATE_QUANTUM
        + PUBLIC_CELL_HALF_STEP
    )


def _plain_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _lower_envelope_bound(value: Decimal, *, world_min: Decimal) -> float:
    as_float = float(value)
    if world_min < value < 0:
        return nextafter(as_float, inf)
    return as_float


def _upper_envelope_bound(value: Decimal, *, world_max: Decimal) -> float:
    as_float = float(value)
    if 0 < value < world_max:
        return nextafter(as_float, -inf)
    return as_float


def _fit_axis_for_public_query(
    lower: Decimal,
    upper: Decimal,
    *,
    world_min: Decimal,
    world_max: Decimal,
) -> tuple[Decimal, Decimal]:
    maximum = settings.PUBLIC_MAP_MAX_BBOX_SPAN_DEGREES
    excess = (upper - lower) - maximum
    if excess <= 0:
        return lower, upper

    if lower == world_min:
        return lower, upper - excess
    if upper == world_max:
        return lower + excess, upper

    lower_shift = excess / 2
    return lower + lower_shift, upper - (excess - lower_shift)
