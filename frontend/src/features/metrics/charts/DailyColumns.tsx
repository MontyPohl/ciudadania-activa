import type { MetricsSeriesPoint } from "@/types/api";
import { ChartFigure, niceMax } from "./ChartFigure";
export function DailyColumns({ points }: { points: MetricsSeriesPoint[] }) {
  const max = niceMax(points.flatMap((p) => [p.received, p.resolved]));
  const width = Math.max(360, points.length * 28 + 40);
  const step = (width - 40) / Math.max(1, points.length);
  return (
    <ChartFigure
      title="Actividad diaria"
      headings={["Fecha", "Recibidos", "Resueltos"]}
      rows={points.map((p) => [p.date, p.received, p.resolved])}
    >
      <p className="mb-3 text-sm text-brand-black/70">
        Reportes recibidos y primeras resoluciones por día. Ambos gráficos usan la misma escala.
      </p>
      <div
        className="overflow-x-auto"
        tabIndex={0}
        role="region"
        aria-label="Gráficos diarios; desplazate para ver todos los días"
      >
        <svg
          viewBox={`0 0 ${width} 370`}
          style={{ minWidth: width }}
          className="w-full"
          aria-hidden="true"
        >
          {(["received", "resolved"] as const).map((key, series) => {
            const top = series * 185;
            return (
              <g key={key}>
                <text x="0" y={top + 16} fontSize="14" className="fill-brand-black">
                  {series === 0 ? "Recibidos" : "Resueltos"}
                </text>
                {[0, max / 2, max].map((value) => {
                  const y = top + 145 - (95 * value) / max;
                  return (
                    <g key={value}>
                      <line x1="34" x2={width} y1={y} y2={y} className="stroke-brand-black/10" />
                      <text x="0" y={y + 4} fontSize="12" className="fill-brand-black/70">
                        {Number.isInteger(value) ? value : ""}
                      </text>
                    </g>
                  );
                })}
                {points.map((p, i) => {
                  const height = (p[key] / max) * 95;
                  const x = 40 + i * step;
                  return (
                    <g key={p.date}>
                      <rect
                        x={x}
                        y={top + 145 - height}
                        width={step - 8}
                        height={height}
                        className={series === 0 ? "fill-brand-purple" : "fill-brand-green"}
                      />
                      <text
                        x={x + (step - 8) / 2}
                        y={top + 139 - height}
                        textAnchor="middle"
                        fontSize="11"
                        className="fill-brand-black"
                      >
                        {p[key]}
                      </text>
                      <text
                        x={x + (step - 8) / 2}
                        y={top + 165}
                        textAnchor="middle"
                        fontSize="11"
                        className="fill-brand-black/70"
                      >
                        {p.date.slice(8)}
                      </text>
                    </g>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
    </ChartFigure>
  );
}
