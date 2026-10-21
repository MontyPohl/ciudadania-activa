"use client";

import { useEffect } from "react";
import { useMap } from "react-leaflet";

/** Mantiene el canvas de Leaflet sincronizado con el tamaño real de su contenedor. */
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
        // Leaflet necesita ajustar el pane para conservar el centro geográfico.
        // Con pan: false, un resize desplaza el bbox y puede excluir los pines.
        map.invalidateSize({ animate: false, pan: true });
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
