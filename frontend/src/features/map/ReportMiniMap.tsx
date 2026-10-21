"use client";
import { divIcon } from "leaflet";
import { MapContainer, Marker, TileLayer } from "react-leaflet";
import { MapResizeWatcher } from "@/components/maps/MapResizeWatcher";
import { OSM_ATTRIBUTION, OSM_TILE_URL } from "@/lib/maps/osm";
import { STATUS_LABELS, STATUS_MARKERS } from "./presentation";
import type { PublicReport } from "@/types/api";
export default function ReportMiniMap({ report }: { report: PublicReport }) {
  const marker = STATUS_MARKERS[report.status];
  return (
    <MapContainer
      fadeAnimation={false}
      zoomAnimation={false}
      markerZoomAnimation={false}
      center={[report.latitude, report.longitude]}
      zoom={16}
      scrollWheelZoom={false}
      className="h-72 w-full rounded-xl"
    >
      <TileLayer url={OSM_TILE_URL} attribution={OSM_ATTRIBUTION} />
      <MapResizeWatcher />
      <Marker
        position={[report.latitude, report.longitude]}
        title={`${report.category.name} · ${STATUS_LABELS[report.status]}`}
        icon={divIcon({
          className: `ca-report-marker ca-report-marker--${report.status.toLowerCase()}`,
          html: `<span aria-hidden="true">${marker.symbol}</span>`,
          iconSize: [34, 42],
          iconAnchor: [17, 39],
        })}
      />
    </MapContainer>
  );
}
