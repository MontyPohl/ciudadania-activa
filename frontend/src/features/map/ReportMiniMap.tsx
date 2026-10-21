"use client";
import { MapContainer, Marker, TileLayer } from "react-leaflet";
import { MapResizeWatcher } from "@/components/maps/MapResizeWatcher";
import { OSM_ATTRIBUTION, OSM_TILE_URL } from "@/lib/maps/osm";
import { STATUS_LABELS } from "./presentation";
import { reportMarkerIcon } from "./reportMarkerIcon";
import type { PublicReport } from "@/types/api";
export default function ReportMiniMap({ report }: { report: PublicReport }) {
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
        icon={reportMarkerIcon(report.status)}
      />
    </MapContainer>
  );
}
