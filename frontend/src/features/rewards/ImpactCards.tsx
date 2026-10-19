import type { RewardsSummary } from "@/types/api";
import { number, panel } from "./presentation";
export function ImpactCards({ impact }: { impact: RewardsSummary["impact"] }) {
  return (
    <section aria-labelledby="impact-title" className="space-y-4">
      <h2 id="impact-title" className="text-xl font-bold">
        Tu impacto ciudadano
      </h2>
      <dl className="grid gap-4 sm:grid-cols-3">
        {(
          [
            ["Impacto histórico", impact.historical],
            ["Impacto del mes", impact.current_month],
            ["Pendientes", impact.pending],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className={panel}>
            <dt className="text-sm text-brand-black/75">{label}</dt>
            <dd className="mt-2 text-3xl font-bold tabular-nums text-brand-purple">
              {number(value)}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-sm text-brand-black/75">Los puntos de impacto no se gastan al canjear</p>
    </section>
  );
}
