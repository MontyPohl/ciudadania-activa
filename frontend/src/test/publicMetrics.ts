import { publicCategories } from "./publicCategories";
import type { PublicMetrics } from "../types/api";
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
  trend: Array.from({ length: 6 }, (_, index) => ({
    period: `2026-${String(index + 5).padStart(2, "0")}`,
    received: 20,
    resolved: 10,
    suppressed: [],
  })),
  current_month_by_category: publicCategories.map((category) => ({
    category,
    received: null,
    suppressed: ["received"],
  })),
  institutions: [
    {
      institution: {
        id: "33333333-3333-4333-8333-333333333333",
        name: "Municipalidad",
        slug: "municipalidad",
      },
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
