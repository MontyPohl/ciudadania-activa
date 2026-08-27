"use client";

import { divIcon } from "leaflet";
import { useMemo } from "react";
import { Marker, useMap } from "react-leaflet";

import {
  clustersForViewport,
  createReportClusterIndex,
  isReportCluster,
} from "@/features/map/cluster";
import type { MapBbox } from "@/features/map/bbox";
import { STATUS_LABELS, STATUS_MARKERS } from "@/features/map/presentation";
import type { PublicReport } from "@/types/api";

interface MapMarkersProps {
  reports: PublicReport[];
  bbox: MapBbox;
  zoom: number;
  selectedId: string | null;
  onSelect: (report: PublicReport, trigger?: HTMLElement) => void;
}

export function MapMarkers({ reports, bbox, zoom, selectedId, onSelect }: MapMarkersProps) {
  const map = useMap();
  const index = useMemo(() => createReportClusterIndex(reports), [reports]);
  const features = useMemo(() => clustersForViewport(index, bbox, zoom), [bbox, index, zoom]);

  return features.map((feature) => {
    const [longitude, latitude] = feature.geometry.coordinates;

    if (isReportCluster(feature)) {
      const count = feature.properties.point_count;
      const clusterId = feature.properties.cluster_id;
      const icon = divIcon({
        className: "ca-cluster-icon",
        html: `<span aria-hidden="true">${count}</span>`,
        iconSize: [42, 42],
        iconAnchor: [21, 21],
      });

      return (
        <Marker
          key={`cluster-${clusterId}`}
          position={[latitude, longitude]}
          icon={icon}
          keyboard={false}
          title={`${count} reportes agrupados. Hacé clic para acercar.`}
          eventHandlers={{
            click: () => {
              const expansionZoom = index.getClusterExpansionZoom(clusterId);
              map.setView([latitude, longitude], Math.min(expansionZoom, map.getMaxZoom()));
            },
          }}
        />
      );
    }

    const report = feature.properties.report;
    const marker = STATUS_MARKERS[report.status];
    const selectedClass = report.id === selectedId ? " ca-report-marker--selected" : "";
    const icon = divIcon({
      className: `ca-report-marker ca-report-marker--${report.status.toLowerCase()}${selectedClass}`,
      html: `<span aria-hidden="true">${marker.symbol}</span>`,
      iconSize: [34, 42],
      iconAnchor: [17, 39],
    });

    return (
      <Marker
        key={report.id}
        position={[latitude, longitude]}
        icon={icon}
        keyboard={false}
        title={`${report.category.name}. Estado: ${STATUS_LABELS[report.status]}.`}
        eventHandlers={{
          click: (event) => {
            onSelect(report, event.target.getElement() ?? undefined);
          },
        }}
      />
    );
  });
}
