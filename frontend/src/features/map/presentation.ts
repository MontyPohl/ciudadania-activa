import type { PublicReportPriority, PublicReportStatus } from "@/types/api";

export const STATUS_LABELS: Record<PublicReportStatus, string> = {
  CREADO: "Creado",
  CLASIFICADO: "Clasificado",
  ASIGNADO: "Asignado",
  EN_PROCESO: "En proceso",
  RESUELTO: "Resuelto",
  CERRADO: "Cerrado",
};

export const PRIORITY_LABELS: Record<PublicReportPriority, string> = {
  BAJA: "Baja",
  MEDIA: "Media",
  ALTA: "Alta",
};

export const STATUS_MARKERS: Record<PublicReportStatus, { color: string; symbol: string }> = {
  CREADO: { color: "#6950a1", symbol: "●" },
  CLASIFICADO: { color: "#f17db1", symbol: "◆" },
  ASIGNADO: { color: "#f37043", symbol: "■" },
  EN_PROCESO: { color: "#d79b00", symbol: "▲" },
  RESUELTO: { color: "#00b26b", symbol: "✓" },
  CERRADO: { color: "#4b5563", symbol: "●" },
};

export function formatPublicDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Fecha no disponible";
  return new Intl.DateTimeFormat("es-PY", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
