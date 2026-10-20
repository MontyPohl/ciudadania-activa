import { render, screen } from "@testing-library/react";
import type { InstitutionMetrics } from "@/types/api";
import { institutionMetrics } from "../../../../e2e/metrics-fixtures";
import { MetricsDashboard } from "../MetricsDashboard";
const base = institutionMetrics();
const withoutSla = {
  ...base,
  sla: {
    hours: null,
    configured: false,
    within: null,
    breached: null,
    compliance_rate: null,
    open_over_sla: null,
  },
} satisfies InstitutionMetrics;
const empty = {
  ...base,
  reports: {
    ...base.reports,
    received: 0,
    by_status: {
      CREADO: 0,
      CLASIFICADO: 0,
      ASIGNADO: 0,
      EN_PROCESO: 0,
      RESUELTO: 0,
      CERRADO: 0,
      RECHAZADO: 0,
    },
    by_priority: { ALTA: 0, MEDIA: 0, BAJA: 0 },
    by_category: [],
    resolution_rate: null,
  },
  decisions: { validated: 0, rejected: 0, validation_rate: null, rejection_rate: null },
  resolution: { resolved: 0, median_hours: null, mean_hours: null },
  incentives: {
    points_credited: 0,
    credits_issued: 0,
    credits_reverted: 0,
    redemptions: 0,
    credits_redeemed: 0,
  },
} satisfies InstitutionMetrics;
it.each([
  {
    name: "con SLA",
    data: {
      ...base,
      scope: "all",
      institution: null,
      by_institution: [
        {
          institution: base.institution!,
          received: 42,
          validated: 30,
          rejected: 4,
          resolved: 16,
          median_hours: 40,
          sla: { hours: 48, configured: true, compliance_rate: 0.875 },
        },
      ],
    } satisfies InstitutionMetrics,
    text: "88 %",
    table: true,
    empty: false,
  },
  { name: "sin SLA", data: withoutSla, text: "Sin SLA definido", table: false, empty: false },
  {
    name: "mes vacío",
    data: empty,
    text: "Todavía no hay reportes en octubre de 2026.",
    table: false,
    empty: true,
  },
])("muestra el dashboard $name", ({ data, text, table, empty }) => {
  render(<MetricsDashboard metrics={data} />);
  expect(screen.getAllByText(text).length).toBeGreaterThan(0);
  expect(screen.queryByRole("table", { name: "Métricas por institución" }) !== null).toBe(table);
  expect(screen.queryByRole("figure", { name: "Actividad diaria" }) === null).toBe(empty);
  expect(screen.queryByRole("figure", { name: "Tendencia de los últimos 6 meses" }) === null).toBe(
    empty,
  );
});
