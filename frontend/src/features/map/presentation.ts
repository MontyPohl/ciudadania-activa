import type { PublicReportStatus } from "@/types/api";
import {
  formatReportDate,
  REPORT_PRIORITY_LABELS,
  REPORT_STATUS_LABELS,
} from "@/features/reports/presentation";

export const STATUS_LABELS: Record<PublicReportStatus, string> = {
  CREADO: REPORT_STATUS_LABELS.CREADO,
  CLASIFICADO: REPORT_STATUS_LABELS.CLASIFICADO,
  ASIGNADO: REPORT_STATUS_LABELS.ASIGNADO,
  EN_PROCESO: REPORT_STATUS_LABELS.EN_PROCESO,
  RESUELTO: REPORT_STATUS_LABELS.RESUELTO,
  CERRADO: REPORT_STATUS_LABELS.CERRADO,
};

export const PRIORITY_LABELS = REPORT_PRIORITY_LABELS;

export const STATUS_MARKERS: Record<PublicReportStatus, { color: string; symbol: string }> = {
  CREADO: { color: "var(--color-map-pending)", symbol: "●" },
  CLASIFICADO: { color: "var(--color-map-pending)", symbol: "●" },
  ASIGNADO: { color: "var(--color-map-pending)", symbol: "●" },
  EN_PROCESO: { color: "var(--color-map-in-progress)", symbol: "▲" },
  RESUELTO: { color: "var(--color-map-resolved)", symbol: "✓" },
  CERRADO: { color: "var(--color-map-resolved)", symbol: "✓" },
};

export function formatPublicDate(value: string): string {
  return formatReportDate(value);
}
