export interface LocationValue {
  latitude: number;
  longitude: number;
}

export type CoordinateAxis = keyof LocationValue;

export const DEFAULT_LOCATION_CENTER: LocationValue = {
  latitude: -25.275,
  longitude: -57.55,
};

export const DEFAULT_LOCATION_ZOOM = 13;

export function isValidLatitude(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= -90 && value <= 90;
}

export function isValidLongitude(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= -180 && value <= 180;
}

export function isValidLocation(value: unknown): value is LocationValue {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<LocationValue>;
  return isValidLatitude(candidate.latitude) && isValidLongitude(candidate.longitude);
}

export function parseCoordinateDraft(value: string, axis: CoordinateAxis): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const parsed = Number(trimmed);
  if (axis === "latitude") return isValidLatitude(parsed) ? parsed : null;
  return isValidLongitude(parsed) ? parsed : null;
}

/**
 * Formatea solo la representación visible. El resultado nunca debe reemplazar
 * una coordenada canónica que el usuario no haya editado.
 */
export function formatCoordinate(value: number): string {
  if (!Number.isFinite(value)) return "";
  return value
    .toFixed(6)
    .replace(/\.?0+$/, "")
    .replace(/^-0$/, "0");
}

export function sameLocation(first: LocationValue | null, second: LocationValue | null): boolean {
  if (first === null || second === null) return first === second;
  return first.latitude === second.latitude && first.longitude === second.longitude;
}
