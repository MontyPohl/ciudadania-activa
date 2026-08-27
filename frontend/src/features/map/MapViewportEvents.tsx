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

/** Mantiene el canvas sincronizado con paneles y cambios responsive sin bucles de resize. */
export function MapResizeWatcher() {
  const map = useMap();

  useEffect(() => {
    const container = map.getContainer().parentElement;
    if (container === null || typeof ResizeObserver === "undefined") return;

    let frame: number | null = null;
    let lastWidth = 0;
    let lastHeight = 0;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width === lastWidth && height === lastHeight) return;
      lastWidth = width;
      lastHeight = height;
      if (frame !== null) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        frame = null;
        map.invalidateSize({ animate: false, pan: false });
      });
    });

    observer.observe(container);
    return () => {
      observer.disconnect();
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [map]);

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
