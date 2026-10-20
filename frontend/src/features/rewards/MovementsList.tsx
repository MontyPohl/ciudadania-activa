"use client";
import { Card } from "@/components/ui/Card";
import { getLedgerMovements } from "@/lib/api/rewards";
import { PaginatedList } from "./PaginatedList";
import { movementLabel } from "./movementLabel";
import { number, date } from "./presentation";
const statusLabels = {
  ACREDITADO: "Acreditado",
  PENDIENTE: "Pendiente",
  ANULADO: "No llegó a acreditarse",
  REVERTIDO: "Revertido por una decisión administrativa",
};
const load = (page: number, signal: AbortSignal) => getLedgerMovements({ page }, signal);
export function MovementsList() {
  return (
    <Card
      as="section"
      id="movimientos"
      aria-labelledby="movements-title"
      className="space-y-4 scroll-mt-6"
    >
      <h2 id="movements-title" className="text-xl font-bold">
        Mis movimientos
      </h2>
      <PaginatedList
        load={load}
        empty="Todavía no tenés movimientos."
        render={(items) => (
          <ul className="divide-y divide-border">
            {items.map((item) => (
              <li
                key={item.id}
                className={`space-y-1 py-4 ${item.status === "ANULADO" ? "text-muted" : item.status === "REVERTIDO" ? "rounded-lg bg-warning/5 px-3 text-warning-ink" : ""}`}
              >
                <p className="font-semibold">{movementLabel(item)}</p>
                <p className="text-sm">
                  {statusLabels[item.status]} · {date(item.created_at)}
                </p>
                <p className="text-sm tabular-nums">
                  Impacto: {item.impact_delta > 0 ? "+" : ""}
                  {number(item.impact_delta)} · Créditos: {item.credit_delta > 0 ? "+" : ""}
                  {number(item.credit_delta)}
                  {item.status === "PENDIENTE" || item.status === "ANULADO"
                    ? " (sin acreditar)"
                    : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      />
    </Card>
  );
}
