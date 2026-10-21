import { divIcon } from "leaflet";

import { STATUS_MARKERS } from "@/features/map/presentation";
import type { PublicReportStatus } from "@/types/api";

/**
 * Leaflet posiciona el contenedor con `transform`, así que la rotación del pin vive en un
 * elemento interno. La punta queda en (17, 38): centro del cuadrado de 30 px más su semidiagonal.
 */
export function reportMarkerIcon(status: PublicReportStatus, selected = false) {
  const selectedClass = selected ? " ca-report-marker--selected" : "";
  return divIcon({
    className: `ca-report-marker ca-report-marker--${status.toLowerCase()}${selectedClass}`,
    html: `<span class="ca-report-pin" aria-hidden="true"><span>${STATUS_MARKERS[status].symbol}</span></span>`,
    iconSize: [34, 42],
    iconAnchor: [17, 38],
    popupAnchor: [0, -34],
  });
}
