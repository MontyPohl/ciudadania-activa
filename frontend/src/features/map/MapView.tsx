"use client";

import { Button } from "@/components/ui/Button";
import { Plus } from "lucide-react";
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
  detailHref: (report: PublicReport) => string;
  reports: PublicReport[];
  viewport: { bbox: MapBbox; zoom: number } | null;
  selectedId: string | null;
  focusTarget: MapFocusTarget | null;
  onViewport: (bbox: MapBbox, zoom: number) => void;
  onSelect: (report: PublicReport, trigger?: HTMLElement) => void;
}

export default function MapView({
  detailHref,
  reports,
  viewport,
  selectedId,
  focusTarget,
  onViewport,
  onSelect,
}: MapViewProps) {
  return (
    <div className="relative h-[calc(100dvh-var(--public-header-height)-5rem)] min-h-[32rem] overflow-hidden bg-canvas lg:h-full">
      <MapContainer
        fadeAnimation={false}
        zoomAnimation={false}
        markerZoomAnimation={false}
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
            detailHref={detailHref}
            reports={reports}
            bbox={viewport.bbox}
            zoom={viewport.zoom}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        )}
      </MapContainer>
      <MapLegend />
      <Button href="/reportes/nuevo" className="absolute right-3 bottom-8 z-[500] shadow-lg">
        <Plus size={18} aria-hidden="true" />
        Crear reporte
      </Button>
    </div>
  );
}
