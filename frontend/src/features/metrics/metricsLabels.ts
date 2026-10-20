import type { ReportPriority, ReportStatus } from "@/types/domain";
export const statusOrder: ReportStatus[] = [
  "CREADO",
  "CLASIFICADO",
  "ASIGNADO",
  "EN_PROCESO",
  "RESUELTO",
  "CERRADO",
  "RECHAZADO",
];
export const priorityOrder: ReportPriority[] = ["ALTA", "MEDIA", "BAJA"];
export const statusLabel = (status: ReportStatus) =>
  ({
    CREADO: "Creado",
    CLASIFICADO: "Clasificado",
    ASIGNADO: "Asignado",
    EN_PROCESO: "En proceso",
    RESUELTO: "Resuelto",
    CERRADO: "Cerrado",
    RECHAZADO: "Rechazado",
  })[status];
export const priorityLabel = (priority: ReportPriority) =>
  ({ ALTA: "Alta", MEDIA: "Media", BAJA: "Baja" })[priority];
export const formatNumber = (value: number) => new Intl.NumberFormat("es-PY").format(value);
export const formatRate = (value: number) => `${Math.round(value * 100)} %`;
export function formatHours(value: number): string {
  const rounded = Math.round(value);
  return value >= 24
    ? `${Math.floor(rounded / 24)} d ${rounded % 24} h`
    : `${formatNumber(value)} h`;
}
export const monthLabel = (period: string) =>
  new Intl.DateTimeFormat("es-PY", { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(`${period}-01T12:00:00Z`),
  );
export const shortMonth = (period: string) =>
  new Intl.DateTimeFormat("es-PY", { month: "short", timeZone: "UTC" })
    .format(new Date(`${period}-01T12:00:00Z`))
    .replace(".", "");
export function currentPeriod(): string {
  const parts = new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "2-digit",
    timeZone: "America/Asuncion",
  }).formatToParts(new Date());
  return `${parts.find((p) => p.type === "year")!.value}-${parts.find((p) => p.type === "month")!.value}`;
}
export const longDate = (date: string) =>
  new Intl.DateTimeFormat("es-PY", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T12:00:00Z`));
