import type { ReportPriority, ReportStatus } from "@/types/domain";

export const REPORT_STATUS_ORDER = [
  "CREADO",
  "CLASIFICADO",
  "ASIGNADO",
  "EN_PROCESO",
  "RESUELTO",
  "CERRADO",
  "RECHAZADO",
] as const satisfies readonly ReportStatus[];

export const REPORT_STATUS_LABELS: Record<ReportStatus, string> = {
  CREADO: "Creado",
  CLASIFICADO: "Clasificado",
  ASIGNADO: "Asignado",
  EN_PROCESO: "En proceso",
  RESUELTO: "Resuelto",
  CERRADO: "Cerrado",
  RECHAZADO: "Rechazado",
};

export const REPORT_PRIORITY_LABELS: Record<ReportPriority, string> = {
  BAJA: "Baja",
  MEDIA: "Media",
  ALTA: "Alta",
};

export function formatReportDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Fecha no disponible";
  return new Intl.DateTimeFormat("es-PY", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
