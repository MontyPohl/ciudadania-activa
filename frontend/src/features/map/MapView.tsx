"use client";

import { MapContainer, TileLayer } from "react-leaflet";

import { MapResizeWatcher } from "@/components/maps/MapResizeWatcher";
import { INITIAL_MAP_BOUNDS, isQueryableBbox, type MapBbox } from "@/features/map/bbox";
import { MapLegend } from "@/features/map/MapLegend";
import { MapMarkers } from "@/features/map/MapMarkers";
import {
  MapDeepLinkFocus,
  MapViewportEvents,
  type MapFocusTarget,
} from "@/features/map/MapViewportEvents";
import { OSM_ATTRIBUTION, OSM_TILE_URL } from "@/lib/maps/osm";
import type { PublicReport } from "@/types/api";

interface MapViewProps {
  reports: PublicReport[];
  viewport: { bbox: MapBbox; zoom: number } | null;
  selectedId: string | null;
  focusTarget: MapFocusTarget | null;
  onViewport: (bbox: MapBbox, zoom: number) => void;
  onSelect: (report: PublicReport, trigger?: HTMLElement) => void;
}

export default function MapView({
  reports,
  viewport,
  selectedId,
  focusTarget,
  onViewport,
  onSelect,
}: MapViewProps) {
  return (
    <div className="relative h-[32rem] min-h-[28rem] overflow-hidden rounded-2xl border border-black/10 bg-brand-purple/5 lg:h-[calc(100vh-13rem)]">
      <MapContainer
        bounds={INITIAL_MAP_BOUNDS}
        boundsOptions={{ padding: [20, 20] }}
        maxZoom={19}
        scrollWheelZoom
        className="h-full w-full"
      >
        <TileLayer url={OSM_TILE_URL} attribution={OSM_ATTRIBUTION} maxZoom={19} />
        <MapViewportEvents onViewport={onViewport} />
        <MapResizeWatcher />
        <MapDeepLinkFocus target={focusTarget} />
        {viewport !== null && isQueryableBbox(viewport.bbox) && (
          <MapMarkers
            reports={reports}
            bbox={viewport.bbox}
            zoom={viewport.zoom}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        )}
      </MapContainer>
      <MapLegend />
    </div>
  );
}
