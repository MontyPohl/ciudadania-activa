import { INITIAL_BBOX, MAX_BBOX_SPAN } from "@/features/map/mapConfig";

export interface MapBbox {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface BoundsLike {
  getWest(): number;
  getSouth(): number;
  getEast(): number;
  getNorth(): number;
}

export const INITIAL_MAP_BOUNDS: [[number, number], [number, number]] = [
  [INITIAL_BBOX.south, INITIAL_BBOX.west],
  [INITIAL_BBOX.north, INITIAL_BBOX.east],
];

export function boundsToBbox(bounds: BoundsLike): MapBbox {
  return {
    west: bounds.getWest(),
    south: bounds.getSouth(),
    east: bounds.getEast(),
    north: bounds.getNorth(),
  };
}

export function isQueryableBbox(bbox: MapBbox): boolean {
  const values = [bbox.west, bbox.south, bbox.east, bbox.north];
  return (
    values.every(Number.isFinite) &&
    bbox.west >= -180 &&
    bbox.east <= 180 &&
    bbox.south >= -90 &&
    bbox.north <= 90 &&
    bbox.west < bbox.east &&
    bbox.south < bbox.north &&
    bbox.east - bbox.west <= MAX_BBOX_SPAN &&
    bbox.north - bbox.south <= MAX_BBOX_SPAN
  );
}

function inwardCoordinate(value: number, lowerBound: boolean, precision: number): string {
  const scale = 10 ** precision;
  const scaled = value * scale;
  const units = lowerBound ? Math.ceil(scaled - 1e-9) : Math.floor(scaled + 1e-9);
  return (units / scale).toFixed(precision);
}

export function serializeBbox(bbox: MapBbox): string {
  const values = [bbox.west, bbox.south, bbox.east, bbox.north];
  if (!values.every(Number.isFinite)) {
    throw new RangeError("No se puede serializar un bbox con coordenadas no finitas.");
  }

  for (const precision of [6, 9]) {
    const serialized = [
      inwardCoordinate(bbox.west, true, precision),
      inwardCoordinate(bbox.south, true, precision),
      inwardCoordinate(bbox.east, false, precision),
      inwardCoordinate(bbox.north, false, precision),
    ].join(",");
    const [west, south, east, north] = serialized.split(",").map(Number);
    if (isQueryableBbox({ west, south, east, north })) return serialized;
  }

  throw new RangeError("El bbox no conserva un área consultable al serializarse.");
}
