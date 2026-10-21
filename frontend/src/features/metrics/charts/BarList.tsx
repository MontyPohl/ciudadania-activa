import { ChartFigure, niceMax } from "./ChartFigure";
import { formatNumber } from "../metricsLabels";
export interface BarDatum {
  label: string;
  value: number | null;
}
export function BarList({ title, items }: { title: string; items: BarDatum[] }) {
  const max = niceMax(items.flatMap((item) => (item.value === null ? [] : [item.value])));
  return (
    <ChartFigure
      title={title}
      headings={["Descripción", "Cantidad"]}
      rows={items.map(({ label, value }) => [label, value ?? "—"])}
    >
      {items.every((item) => item.value === 0) ? (
        <p className="py-6 text-sm text-muted">Todavía no hay datos.</p>
      ) : (
        <div aria-hidden="true" className="space-y-4">
          {items.map(({ label, value }) => (
            <div key={label}>
              <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 break-words">{label}</span>
                <span className="shrink-0 font-semibold tabular-nums">
                  {value === null ? "—" : formatNumber(value)}
                </span>
              </div>
              <svg viewBox="0 0 300 12" className="h-3 w-full" preserveAspectRatio="none">
                <path d="M0 6H300" className="stroke-primary/10" strokeWidth="12" />
                <rect
                  width={value === null ? 0 : (300 * value) / max}
                  height="12"
                  rx="3"
                  className="fill-primary"
                />
              </svg>
            </div>
          ))}
          <div className="flex justify-between text-xs text-muted">
            <span>0</span>
            <span>{formatNumber(max)}</span>
          </div>
        </div>
      )}
    </ChartFigure>
  );
}
