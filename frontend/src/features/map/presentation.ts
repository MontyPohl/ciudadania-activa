import type { PublicReportStatus } from "@/types/api";
import {
  formatReportDate,
  REPORT_PRIORITY_LABELS,
  REPORT_STATUS_LABELS,
} from "@/features/reports/presentation";

export const STATUS_LABELS: Record<PublicReportStatus, string> = REPORT_STATUS_LABELS;

export const PRIORITY_LABELS = REPORT_PRIORITY_LABELS;

export const STATUS_MARKERS: Record<PublicReportStatus, { color: string; symbol: string }> = {
  CREADO: { color: "#6950a1", symbol: "●" },
  CLASIFICADO: { color: "#f17db1", symbol: "◆" },
  ASIGNADO: { color: "#f37043", symbol: "■" },
  EN_PROCESO: { color: "#d79b00", symbol: "▲" },
  RESUELTO: { color: "#00b26b", symbol: "✓" },
  CERRADO: { color: "#4b5563", symbol: "●" },
};

export function formatPublicDate(value: string): string {
  return formatReportDate(value);
}
