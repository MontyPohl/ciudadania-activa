import { zoneSummary } from "../ZoneMetrics";
import { publicReport } from "@/test/fixtures";
it("cuenta solo abiertos de más de siete días y calcula densidad de la zona consultada", () => {
  const now = Date.parse("2026-10-20T12:00:00Z");
  const old = publicReport({ created_at: "2026-10-12T12:00:00Z" });
  const summary = zoneSummary(
    [old, { ...old, status: "CERRADO" }, { ...old, created_at: "2026-10-13T12:00:00Z" }],
    now,
  );
  expect(summary.oldOpen).toBe(1);
  expect(summary.density).toBeGreaterThan(0);
  expect(zoneSummary([], now)).toEqual({ oldOpen: 0, density: 0 });
});
