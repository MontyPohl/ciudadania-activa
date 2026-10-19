import { movementLabel } from "../movementLabel";
import type { RewardEventType, RewardTxStatus } from "@/types/domain";
const events: RewardEventType[] = [
  "REPORT_CREATED",
  "CONFIRMATION_CREATED",
  "VALIDATION_BONUS",
  "RESOLUTION_BONUS",
  "REDEMPTION",
  "REVERSAL",
];
const statuses: RewardTxStatus[] = ["PENDIENTE", "ACREDITADO", "ANULADO", "REVERTIDO"];
const pending: Record<RewardEventType, string> = {
  REPORT_CREATED: "Reporte pendiente de validación",
  CONFIRMATION_CREATED: "Confirmación pendiente de validación",
  VALIDATION_BONUS: "Bono por validación pendiente",
  RESOLUTION_BONUS: "Bono por resolución pendiente",
  REDEMPTION: "Canje de recompensa de ANDE",
  REVERSAL: "Revertido por una decisión administrativa",
};
const credited: Record<RewardEventType, string> = {
  REPORT_CREATED: "Reporte validado por ANDE",
  CONFIRMATION_CREATED: "Confirmación validada por ANDE",
  VALIDATION_BONUS: "Bono por validación · ANDE",
  RESOLUTION_BONUS: "Bono por resolución · ANDE",
  REDEMPTION: "Canje de recompensa de ANDE",
  REVERSAL: "Revertido por una decisión administrativa",
};
describe("movementLabel", () => {
  it.each(events.flatMap((event_type) => statuses.map((status) => ({ event_type, status }))))(
    "describe $event_type / $status",
    ({ event_type, status }) => {
      const expected =
        status === "ANULADO"
          ? "No llegó a acreditarse"
          : status === "REVERTIDO"
            ? "Revertido por una decisión administrativa"
            : status === "PENDIENTE"
              ? pending[event_type]
              : credited[event_type];
      expect(
        movementLabel({
          event_type,
          status,
          institution: { id: "22222222-2222-4222-8222-222222222222", name: "ANDE", slug: "ande" },
        }),
      ).toBe(expected);
      if (status === "PENDIENTE")
        expect(movementLabel({ event_type, status, institution: null })).toBe(
          event_type === "REDEMPTION" ? "Canje de recompensa" : expected,
        );
    },
  );
});
