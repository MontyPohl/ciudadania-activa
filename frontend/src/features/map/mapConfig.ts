export const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export const INITIAL_BBOX = {
  west: -57.7,
  south: -25.4,
  east: -57.4,
  north: -25.15,
} as const;

export const MAP_DEBOUNCE_MS = 350;
export const MAX_BBOX_SPAN = 0.5;
