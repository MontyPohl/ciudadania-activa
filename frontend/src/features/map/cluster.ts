import Supercluster from "supercluster";

import type { MapBbox } from "@/features/map/bbox";
import type { PublicReport } from "@/types/api";

export interface ReportPointProperties {
  report: PublicReport;
}

export type ReportPoint = Supercluster.PointFeature<ReportPointProperties>;
export type ReportCluster = Supercluster.ClusterFeature<Supercluster.AnyProps>;

export function reportToPoint(report: PublicReport): ReportPoint {
  return {
    type: "Feature",
    id: report.id,
    properties: { report },
    geometry: {
      type: "Point",
      coordinates: [report.longitude, report.latitude],
    },
  };
}

export function createReportClusterIndex(
  reports: PublicReport[],
): Supercluster<ReportPointProperties> {
  return new Supercluster<ReportPointProperties>({ radius: 60, maxZoom: 18 }).load(
    reports.map(reportToPoint),
  );
}

export function clustersForViewport(
  index: Supercluster<ReportPointProperties>,
  bbox: MapBbox,
  zoom: number,
): Array<ReportPoint | ReportCluster> {
  return index.getClusters(
    [bbox.west, bbox.south, bbox.east, bbox.north],
    Math.max(0, Math.min(20, Math.floor(zoom))),
  );
}

export function isReportCluster(feature: ReportPoint | ReportCluster): feature is ReportCluster {
  return "cluster" in feature.properties && feature.properties.cluster === true;
}
