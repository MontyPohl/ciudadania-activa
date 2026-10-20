"use client";
import { Card } from "@/components/ui/Card";
import Link from "next/link";
import { getMyRedemptions } from "@/lib/api/rewards";
import { PaginatedList } from "./PaginatedList";
import { date } from "./presentation";
const load = (page: number, signal: AbortSignal) => getMyRedemptions({ page }, signal);
export function MyRedemptionsList() {
  return (
    <Card as="section" aria-labelledby="redemptions-title" className="space-y-4">
      <h2 id="redemptions-title" className="text-xl font-bold">
        Mis canjes
      </h2>
      <PaginatedList
        load={load}
        empty="Todavía no canjeaste recompensas."
        render={(items) => (
          <ul className="divide-y divide-border">
            {items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-4">
                <div>
                  <p className="font-semibold">
                    {item.reward.name} · {item.institution.name}
                  </p>
                  <p className="text-sm text-muted">{date(item.created_at)}</p>
                </div>
                <Link
                  className="inline-flex min-h-11 items-center text-primary underline"
                  href={`/recompensas/canjes/${item.id}`}
                >
                  Ver mi vale
                </Link>
              </li>
            ))}
          </ul>
        )}
      />
    </Card>
  );
}
