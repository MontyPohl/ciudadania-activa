import type { MetricsTrendPoint } from "@/types/api";
import { ChartFigure, niceMax } from "./ChartFigure";
import { monthLabel, shortMonth } from "../metricsLabels";
export function TrendColumns({ points }: { points: MetricsTrendPoint[] }) {
  const max = niceMax(points.flatMap((p) => [p.received, p.resolved]));
  return (
    <ChartFigure
      title="Tendencia de los últimos 6 meses"
      headings={["Mes", "Recibidos", "Resueltos"]}
      rows={points.map((p) => [monthLabel(p.period), p.received, p.resolved])}
    >
      <p className="mb-3 flex flex-wrap gap-4 text-sm">
        <span>
          <span aria-hidden="true" className="mr-2 inline-block h-3 w-3 bg-brand-purple" />
          Recibidos · izquierda
        </span>
        <span>
          <span aria-hidden="true" className="mr-2 inline-block h-3 w-3 bg-brand-green" />
          Resueltos · derecha
        </span>
      </p>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Tendencia mensual">
        <svg viewBox="0 0 420 220" className="mx-auto w-full min-w-80 max-w-xl" aria-hidden="true">
          {[0, max / 2, max].map((value) => (
            <g key={value}>
              <line
                x1="30"
                x2="420"
                y1={175 - (130 * value) / max}
                y2={175 - (130 * value) / max}
                className="stroke-brand-black/10"
              />
              <text
                x="0"
                y={179 - (130 * value) / max}
                fontSize="12"
                className="fill-brand-black/70"
              >
                {Number.isInteger(value) ? value : ""}
              </text>
            </g>
          ))}
          {points.map((p, i) => (
            <g key={p.period}>
              {(["received", "resolved"] as const).map((key, j) => {
                const height = (130 * p[key]) / max;
                const x = 38 + i * 64 + j * 25;
                return (
                  <g key={key}>
                    <rect
                      x={x}
                      y={175 - height}
                      width="20"
                      height={height}
                      className={j === 0 ? "fill-brand-purple" : "fill-brand-green"}
                    />
                    <text
                      x={x + 10}
                      y={169 - height}
                      fontSize="11"
                      textAnchor="middle"
                      className="fill-brand-black"
                    >
                      {p[key]}
                    </text>
                  </g>
                );
              })}
              <text
                x={60 + i * 64}
                y="200"
                textAnchor="middle"
                fontSize="13"
                className="fill-brand-black/70"
              >
                {shortMonth(p.period)}
              </text>
            </g>
          ))}
        </svg>
      </div>
    </ChartFigure>
  );
}
