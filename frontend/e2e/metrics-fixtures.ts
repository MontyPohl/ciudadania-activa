import type { Page } from "@playwright/test";
import type { InstitutionMetrics, PublicMetrics } from "../src/types/api";
import { MUNICIPAL_ID, ANDE_ID } from "./operator-report-fixtures";
const municipal = { id: MUNICIPAL_ID, name: "Municipalidad", slug: "municipalidad" };
const ande = { id: ANDE_ID, name: "ANDE", slug: "ande" };
export function institutionMetrics(period = "2026-10", received = 42): InstitutionMetrics {
  const [year, month] = period.split("-").map(Number);
  const next = new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 7);
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return {
    scope: "institution",
    institution: municipal,
    period,
    period_start: `${period}-01T00:00:00-03:00`,
    period_end: `${next}-01T00:00:00-03:00`,
    is_current_period: false,
    generated_at: "2026-10-20T10:15:02-03:00",
    reports: {
      received,
      by_status: {
        CREADO: received - 39,
        CLASIFICADO: 5,
        ASIGNADO: 8,
        EN_PROCESO: 6,
        RESUELTO: 4,
        CERRADO: 12,
        RECHAZADO: 4,
      },
      by_priority: { ALTA: 10, MEDIA: received - 17, BAJA: 7 },
      by_category: [
        {
          category: {
            id: "44444444-4444-4444-8444-444444444444",
            name: "Alumbrado público",
            slug: "alumbrado-publico",
          },
          count: received,
        },
      ],
      resolution_rate: 0.421,
    },
    decisions: { validated: 30, rejected: 4, validation_rate: 0.882, rejection_rate: 0.118 },
    resolution: { resolved: 16, mean_hours: 52.4, median_hours: 40 },
    sla: {
      configured: true,
      hours: 48,
      within: 14,
      breached: 2,
      compliance_rate: 0.875,
      open_over_sla: 2,
    },
    confirmations: { count: 57 },
    incentives: {
      points_credited: 1480,
      credits_issued: 1480,
      credits_reverted: 30,
      redemptions: 1,
      credits_redeemed: 1000,
    },
    series: {
      granularity: "day",
      points: Array.from({ length: days }, (_, i) => ({
        date: `${period}-${String(i + 1).padStart(2, "0")}`,
        received: i === 0 ? received : 0,
        resolved: i === 0 ? 16 : 0,
      })),
    },
    trend: Array.from({ length: 6 }, (_, i) => ({
      period: new Date(Date.UTC(year, month - 6 + i, 1)).toISOString().slice(0, 7),
      received: i === 5 ? received : 10,
      resolved: i === 5 ? 16 : 6,
    })),
    by_institution: null,
  };
}
export const publicMetrics: PublicMetrics = {
  window: { from: "2026-05-01", to: "2026-10-20", months: 6 },
  generated_at: "2026-10-20T10:15:02-03:00",
  min_cell_size: 5,
  totals: {
    received: 120,
    validated: 80,
    rejected: 10,
    resolved: 60,
    resolution_rate: 0.545,
    median_resolution_hours: 40,
    confirmations: 210,
    redemptions: null,
    credits_issued: 5000,
    suppressed: ["redemptions"],
  },
  institutions: [
    {
      institution: municipal,
      received: 40,
      validated: 30,
      rejected: 2,
      resolved: 20,
      resolution_rate: 0.526,
      median_resolution_hours: 38.5,
      sla: { configured: false, hours: null, compliance_rate: null },
      confirmations: 60,
      redemptions: null,
      credits_issued: 900,
      suppressed: ["redemptions"],
    },
  ],
};
export async function mockMetricsApi(page: Page, actor: "operator" | "anonymous") {
  const requests: { period: string | null; institutionId: string | null }[] = [];
  await page.route("**/api/auth/refresh", (route) =>
    route.fulfill(
      actor === "operator"
        ? { status: 200, json: { access: "synthetic-operator-access" } }
        : { status: 401, json: { detail: "Sin sesión sintética." } },
    ),
  );
  await page.route("**/api/v1/auth/me/", (route) =>
    route.fulfill(
      actor === "operator"
        ? {
            status: 200,
            json: {
              id: "55555555-5555-4555-8555-555555555555",
              email: "operator@example.invalid",
              full_name: "Operador E2E",
              role: "operator",
              verification_status: "PENDIENTE",
              is_verified_citizen: false,
              institutions: [municipal, ande],
            },
          }
        : { status: 401, json: { detail: "Sin sesión sintética." } },
    ),
  );
  await page.route("**/api/v1/operator/metrics/**", async (route) => {
    const query = new URL(route.request().url()).searchParams;
    requests.push({ period: query.get("period"), institutionId: query.get("institution_id") });
    const period = query.get("period") ?? "2026-10";
    const data = institutionMetrics(period, period === "2026-01" ? 55 : 42);
    data.institution = query.get("institution_id") === ANDE_ID ? ande : municipal;
    await route.fulfill({ status: 200, json: data });
  });
  await page.route("**/api/v1/public/metrics/", (route) =>
    route.fulfill({ status: 200, json: publicMetrics }),
  );
  return requests;
}
