import type { LedgerMovement } from "@/types/api";
export function movementLabel({
  event_type,
  status,
  institution,
}: Pick<LedgerMovement, "event_type" | "status" | "institution">): string {
  if (status === "ANULADO") return "No llegó a acreditarse";
  if (status === "REVERTIDO" || event_type === "REVERSAL")
    return "Revertido por una decisión administrativa";
  if (event_type === "REDEMPTION")
    return `Canje de recompensa${institution ? ` de ${institution.name}` : ""}`;
  const by = institution ? ` por ${institution.name}` : "";
  if (status === "PENDIENTE")
    return event_type === "CONFIRMATION_CREATED"
      ? "Confirmación pendiente de validación"
      : event_type === "RESOLUTION_BONUS"
        ? "Bono por resolución pendiente"
        : event_type === "VALIDATION_BONUS"
          ? "Bono por validación pendiente"
          : "Reporte pendiente de validación";
  switch (event_type) {
    case "REPORT_CREATED":
      return `Reporte validado${by}`;
    case "CONFIRMATION_CREATED":
      return `Confirmación validada${by}`;
    case "VALIDATION_BONUS":
      return `Bono por validación${by}`;
    case "RESOLUTION_BONUS":
      return `Bono por resolución${by}`;
  }
}
