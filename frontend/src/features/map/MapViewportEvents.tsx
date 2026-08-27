"use client";

import { useCallback, useEffect, useRef } from "react";
import { useMap, useMapEvents } from "react-leaflet";

import { boundsToBbox, isQueryableBbox, type MapBbox } from "@/features/map/bbox";

export interface MapFocusTarget {
  id: string;
  latitude: number;
  longitude: number;
}

interface MapViewportEventsProps {
  onViewport: (bbox: MapBbox, zoom: number) => void;
}

export function MapViewportEvents({ onViewport }: MapViewportEventsProps) {
  const map = useMap();

  const emitViewport = useCallback(() => {
    onViewport(boundsToBbox(map.getBounds()), map.getZoom());
  }, [map, onViewport]);

  useMapEvents({
    moveend: emitViewport,
    zoomend: emitViewport,
  });

  useEffect(() => {
    let active = true;
    map.whenReady(() => {
      if (!active) return;
      let adjustments = 0;
      while (
        !isQueryableBbox(boundsToBbox(map.getBounds())) &&
        map.getZoom() < map.getMaxZoom() &&
        adjustments < 8
      ) {
        map.setZoom(map.getZoom() + 1, { animate: false });
        adjustments += 1;
      }
      emitViewport();
    });
    return () => {
      active = false;
    };
  }, [emitViewport, map]);

  return null;
}

/** Centra una sola vez un reporte resuelto desde un deep-link, usando precisión pública. */
export function MapDeepLinkFocus({ target }: { target: MapFocusTarget | null }) {
  const map = useMap();
  const focusedId = useRef<string | null>(null);

  useEffect(() => {
    if (target === null) {
      focusedId.current = null;
      return;
    }
    if (focusedId.current === target.id) return;
    focusedId.current = target.id;
    map.setView([target.latitude, target.longitude], Math.min(16, map.getMaxZoom()), {
      animate: false,
    });
  }, [map, target]);

  return null;
}
