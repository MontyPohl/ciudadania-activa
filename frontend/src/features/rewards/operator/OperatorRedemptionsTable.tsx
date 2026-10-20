"use client";
import { Card } from "@/components/ui/Card";
import { useCallback } from "react";
import { getOperatorRedemptions } from "@/lib/api/operatorRewards";
import { PaginatedList } from "../PaginatedList";
import { date } from "../presentation";
export function OperatorRedemptionsTable({ institutionId }: { institutionId: string }) {
  const load = useCallback(
    (page: number, signal: AbortSignal) => getOperatorRedemptions(institutionId, { page }, signal),
    [institutionId],
  );
  return (
    <Card as="section" aria-labelledby="operator-redemptions-title" className="space-y-4">
      <h2 id="operator-redemptions-title" className="text-xl font-bold">
        Canjes de la institución
      </h2>
      <PaginatedList
        load={load}
        empty="Todavía no hay canjes en esta institución."
        render={(items) => (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Vales emitidos por la institución seleccionada</caption>
              <thead>
                <tr className="border-b border-border">
                  {["Código", "Fecha", "Recompensa", "Ciudadano"].map((label) => (
                    <th scope="col" className="px-3 py-3" key={label}>
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className="border-b border-border">
                    <td className="px-3 py-4 font-mono">{item.code}</td>
                    <td className="px-3 py-4">{date(item.created_at)}</td>
                    <td className="px-3 py-4">{item.reward.name}</td>
                    <td className="px-3 py-4">{item.citizen_display}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      />
    </Card>
  );
}
