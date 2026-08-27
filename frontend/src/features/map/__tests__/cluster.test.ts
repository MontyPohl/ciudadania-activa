import {
  clustersForViewport,
  createReportClusterIndex,
  isReportCluster,
  reportToPoint,
} from "@/features/map/cluster";
import { publicReport } from "@/test/fixtures";

describe("datos para Supercluster", () => {
  it("transforma el contrato público a GeoJSON transitorio", () => {
    const report = publicReport();
    const point = reportToPoint(report);
    expect(point.geometry.coordinates).toEqual([report.longitude, report.latitude]);
    expect(point.id).toBe(report.id);
    expect(point.properties.report).toBe(report);
  });

  it("mantiene el orden longitude, latitude con valores no intercambiables", () => {
    const point = reportToPoint(publicReport({ longitude: 120.123, latitude: -7.456 }));
    expect(point.geometry.coordinates).toEqual([120.123, -7.456]);
    expect(point.geometry.coordinates).not.toEqual([-7.456, 120.123]);
  });

  it("agrupa puntos cercanos y expone cantidad", () => {
    const reports = [
      publicReport(),
      publicReport({
        id: "44444444-4444-4444-8444-444444444444",
        latitude: -25.3001,
        longitude: -57.6001,
      }),
    ];
    const index = createReportClusterIndex(reports);
    const features = clustersForViewport(
      index,
      { west: -58, south: -26, east: -57, north: -25 },
      8,
    );
    const cluster = features.find(isReportCluster);
    expect(cluster?.properties.point_count).toBe(2);
  });

  it("calcula un zoom de expansión mayor al zoom agrupado", () => {
    const index = createReportClusterIndex([
      publicReport(),
      publicReport({
        id: "44444444-4444-4444-8444-444444444444",
        latitude: -25.3001,
        longitude: -57.6001,
      }),
    ]);
    const cluster = clustersForViewport(
      index,
      { west: -58, south: -26, east: -57, north: -25 },
      8,
    ).find(isReportCluster);
    expect(cluster).toBeDefined();
    expect(index.getClusterExpansionZoom(cluster!.properties.cluster_id)).toBeGreaterThan(8);
  });
});
