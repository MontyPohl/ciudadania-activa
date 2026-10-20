import type { InstitutionMetrics } from "@/types/api";
import { BarList } from "./charts/BarList";
import { DailyColumns } from "./charts/DailyColumns";
import { TrendColumns } from "./charts/TrendColumns";
import { KpiTile } from "./charts/KpiTile";
import {
  formatHours,
  formatNumber,
  formatRate,
  monthLabel,
  priorityLabel,
  priorityOrder,
  statusLabel,
  statusOrder,
} from "./metricsLabels";
export function MetricsDashboard({ metrics: m }: { metrics: InstitutionMetrics }) {
  const empty = m.reports.received === 0;
  const hasTrend = m.trend.some((point) => point.received > 0 || point.resolved > 0);
  return (
    <div className="space-y-8">
      <div>
        <h2 className="mb-4 text-xl font-semibold">
          {m.institution?.name ?? "Todas las instituciones"} · {monthLabel(m.period)}
        </h2>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <KpiTile
            label="Recibidos"
            value={m.reports.received}
            help="Reportes creados durante el mes."
          />
          <KpiTile
            label="Validados"
            value={m.decisions.validated}
            help="Primera validación durante el mes."
          />
          <KpiTile
            label="Resueltos"
            value={m.resolution.resolved}
            help="Primera resolución durante el mes."
          />
          <KpiTile
            label="Tasa de validación"
            value={
              m.decisions.validation_rate === null ? null : formatRate(m.decisions.validation_rate)
            }
          />
          <KpiTile
            label="Mediana de resolución"
            value={
              m.resolution.median_hours === null ? null : formatHours(m.resolution.median_hours)
            }
          />
          <KpiTile
            label="Cumplimiento de SLA"
            value={
              !m.sla.configured
                ? "Sin SLA definido"
                : m.sla.compliance_rate === null
                  ? null
                  : formatRate(m.sla.compliance_rate)
            }
            help={
              !m.sla.configured
                ? "La institución todavía no fijó un plazo de atención."
                : m.sla.hours === null
                  ? "Sobre las instituciones con un plazo definido."
                  : `Plazo de atención: ${formatHours(m.sla.hours)}.`
            }
          />
        </dl>
      </div>
      {empty ? (
        <p role="status" className="rounded-2xl bg-primary/5 p-6">
          Todavía no hay reportes en {monthLabel(m.period)}.
        </p>
      ) : (
        <>
          <DailyColumns points={m.series.points} />
          <div className="grid min-w-0 gap-4 lg:grid-cols-3">
            <BarList
              title="Por estado actual"
              items={statusOrder.map((status) => ({
                label: statusLabel(status),
                value: m.reports.by_status[status],
              }))}
            />
            <BarList
              title="Por prioridad"
              items={priorityOrder.map((priority) => ({
                label: priorityLabel(priority),
                value: m.reports.by_priority[priority],
              }))}
            />
            <BarList
              title="Por categoría"
              items={[...m.reports.by_category]
                .sort(
                  (a, b) =>
                    b.count - a.count || a.category.name.localeCompare(b.category.name, "es"),
                )
                .map((row) => ({ label: row.category.name, value: row.count }))}
            />
          </div>
        </>
      )}
      <section aria-labelledby="metrics-incentives">
        <h2 id="metrics-incentives" className="mb-4 text-xl font-semibold">
          Incentivos del mes
        </h2>
        <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <KpiTile label="Créditos emitidos" value={m.incentives.credits_issued} />
          <KpiTile label="Créditos revertidos" value={m.incentives.credits_reverted} />
          <KpiTile label="Canjes" value={m.incentives.redemptions} />
          <KpiTile label="Créditos canjeados" value={m.incentives.credits_redeemed} />
        </dl>
      </section>
      {hasTrend && <TrendColumns points={m.trend} />}
      {m.by_institution !== null && (
        <section className="min-w-0 rounded-2xl border border-border p-4 sm:p-6">
          <h2 className="mb-4 text-xl font-semibold">Detalle por institución</h2>
          <div
            className="overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label="Detalle por institución"
          >
            <table className="w-full text-left text-sm tabular-nums">
              <caption className="sr-only">Métricas por institución</caption>
              <thead>
                <tr>
                  {[
                    "Institución",
                    "Recibidos",
                    "Validados",
                    "Rechazados",
                    "Resueltos",
                    "Mediana",
                    "SLA",
                  ].map((label) => (
                    <th key={label} scope="col" className="p-3">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {m.by_institution.map((row) => (
                  <tr key={row.institution.id} className="border-t border-border">
                    <th scope="row" className="p-3 font-medium">
                      {row.institution.name}
                    </th>
                    {[row.received, row.validated, row.rejected, row.resolved].map((v, i) => (
                      <td key={i} className="p-3">
                        {formatNumber(v)}
                      </td>
                    ))}
                    <td className="p-3">
                      {row.median_hours === null ? "—" : formatHours(row.median_hours)}
                    </td>
                    <td className="p-3">
                      {!row.sla.configured
                        ? "Sin SLA definido"
                        : row.sla.compliance_rate === null
                          ? "Sin resoluciones"
                          : formatRate(row.sla.compliance_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <p className="text-sm text-muted">
        Datos calculados a las{" "}
        {new Intl.DateTimeFormat("es-PY", {
          hour: "2-digit",
          minute: "2-digit",
          timeZone: "America/Asuncion",
        }).format(new Date(m.generated_at))}
        . Se actualizan cada pocos minutos.
      </p>
    </div>
  );
}
