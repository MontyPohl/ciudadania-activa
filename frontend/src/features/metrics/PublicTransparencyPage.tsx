"use client";
import { Card } from "@/components/ui/Card";
import { MonthlyRankingPage } from "@/features/ranking/MonthlyRankingPage";
import { ZoneMetrics } from "./ZoneMetrics";
import { TrendColumns } from "./charts/TrendColumns";
import { BarList } from "./charts/BarList";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getPublicMetrics } from "@/lib/api/publicMetrics";
import { PublicApiError } from "@/lib/api/publicReports";
import type { PublicMetrics, PublicMetricsCells, PublicSuppressible } from "@/types/api";
import { KpiTile } from "./charts/KpiTile";
import { MetricsError, MetricsLoading, metricsPageClass } from "./MetricsFeedback";
import { formatHours, formatRate, longDate } from "./metricsLabels";
const suppressedHelp = "Menos de 5 casos: no se publica para proteger la privacidad.";
function PublicCells({ cells }: { cells: PublicMetricsCells }) {
  const derived = (
    key: PublicSuppressible,
    value: number | null,
    format?: (value: number) => string,
  ) => ({
    value: value === null ? null : format ? format(value) : value,
    nullHelp: cells.suppressed.includes(key)
      ? suppressedHelp
      : "Todavía no hay casos para calcular este dato.",
  });
  return (
    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      <KpiTile label="Recibidos" value={cells.received} />
      <KpiTile label="Validados" value={cells.validated} />
      <KpiTile label="Rechazados" value={cells.rejected} />
      <KpiTile label="Resueltos" value={cells.resolved} />
      <KpiTile
        label="Tasa de resolución"
        {...derived("resolution_rate", cells.resolution_rate, formatRate)}
      />
      <KpiTile
        label="Mediana de resolución"
        {...derived("median_resolution_hours", cells.median_resolution_hours, formatHours)}
      />
      <KpiTile label="Confirmaciones" {...derived("confirmations", cells.confirmations)} />
      <KpiTile label="Canjes" {...derived("redemptions", cells.redemptions)} />
      <KpiTile label="Créditos emitidos" {...derived("credits_issued", cells.credits_issued)} />
    </dl>
  );
}
export function PublicTransparencyPage() {
  const [tab, setTab] = useState<"institutions" | "citizens">("institutions");
  const [data, setData] = useState<PublicMetrics | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getPublicMetrics(controller.signal)
      .then((metrics) => {
        if (!controller.signal.aborted) setData(metrics);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setError(
          error instanceof PublicApiError && error.status === 429
            ? "Se alcanzó el límite de consultas. Esperá un momento antes de volver a intentar."
            : "No pudimos cargar las métricas públicas. Intentá de nuevo.",
        );
      });
    return () => controller.abort();
  }, [retry]);
  return (
    <main id="contenido-principal" tabIndex={-1} className={metricsPageClass}>
      <header>
        <p className="text-sm font-semibold text-primary">Ciudadanía Activa · datos abiertos</p>
        <h1 className="mt-1 text-3xl font-bold text-primary">Estadísticas</h1>
        {data && (
          <p className="mt-2 text-muted">
            Del {longDate(data.window.from)} al {longDate(data.window.to)}.
          </p>
        )}
      </header>
      <p className="rounded-2xl bg-primary/5 p-4 text-sm leading-relaxed">
        Para proteger la privacidad, no publicamos cifras que surjan de menos de 5 personas.
      </p>
      {error ? (
        <MetricsError
          message={error}
          onRetry={() => {
            setError("");
            setData(null);
            setRetry((r) => r + 1);
          }}
        />
      ) : !data ? (
        <MetricsLoading />
      ) : (
        <>
          <section aria-labelledby="public-totals">
            <h2 id="public-totals" className="mb-4 text-xl font-semibold">
              Totales del período
            </h2>
            <PublicCells cells={data.totals} />
          </section>
          {data.totals.received === 0 && (
            <p role="status">Todavía no hay reportes en estos 6 meses.</p>
          )}
          <div
            className="flex flex-wrap gap-2"
            role="tablist"
            aria-label="Estadísticas de participación"
          >
            {(
              [
                ["institutions", "Instituciones"],
                ["citizens", "Ciudadanos del mes"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                id={`tab-${value}`}
                type="button"
                role="tab"
                aria-selected={tab === value}
                aria-controls={`panel-${value}`}
                onClick={() => setTab(value)}
                onKeyDown={(event) => {
                  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
                    event.preventDefault();
                    const next =
                      event.key === "Home"
                        ? "institutions"
                        : event.key === "End"
                          ? "citizens"
                          : tab === "institutions"
                            ? "citizens"
                            : "institutions";
                    setTab(next);
                    document.getElementById(`tab-${next}`)?.focus();
                  }
                }}
                tabIndex={tab === value ? 0 : -1}
                className="min-h-11 rounded-lg px-4 font-semibold text-primary aria-selected:bg-primary aria-selected:text-on-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                {label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0}>
            {tab === "citizens" ? (
              <MonthlyRankingPage embedded />
            ) : (
              <Card as="section" className="space-y-4">
                <h2 className="text-xl font-semibold">Instituciones</h2>
                <p className="text-sm text-muted">
                  Orden alfabético. “—” indica un dato suprimido por privacidad o sin casos
                  suficientes para calcularlo. Las supresiones complementarias también pueden
                  ocultar cifras de 5 o más casos.
                </p>
                <div
                  className="max-w-full overflow-x-auto"
                  role="region"
                  aria-label="Tabla de instituciones"
                  tabIndex={0}
                >
                  <table className="w-full text-left text-sm tabular-nums">
                    <caption className="sr-only">Gestión por institución</caption>
                    <thead>
                      <tr>
                        {[
                          "Institución",
                          "Recibidos",
                          "Resueltos",
                          "Tasa de resolución",
                          "Mediana de horas",
                          "SLA",
                        ].map((label) => (
                          <th scope="col" key={label} className="p-3">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {[...data.institutions]
                        .sort((a, b) => a.institution.name.localeCompare(b.institution.name, "es"))
                        .map((row) => (
                          <tr key={row.institution.id} className="border-t border-border">
                            <th scope="row" className="p-3 font-medium">
                              {row.institution.name}
                            </th>
                            {[
                              row.received,
                              row.resolved,
                              row.resolution_rate === null ? "—" : formatRate(row.resolution_rate),
                              row.median_resolution_hours === null
                                ? "—"
                                : formatHours(row.median_resolution_hours),
                              row.sla.compliance_rate === null
                                ? row.sla.configured
                                  ? "—"
                                  : "Sin SLA definido"
                                : formatRate(row.sla.compliance_rate),
                            ].map((value, i) => (
                              <td key={i} className="p-3">
                                {value}
                              </td>
                            ))}
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {data.institutions.length === 0 && (
                  <p role="status">No hay instituciones para mostrar.</p>
                )}
              </Card>
            )}
          </div>
          <p className="text-sm text-muted">
            En los gráficos, “—” indica una celda suprimida por privacidad. No equivale a cero.
          </p>
          <div className="grid min-w-0 gap-6 lg:grid-cols-2">
            <TrendColumns points={data.trend} />
            <BarList
              title="Reportes por categoría del mes actual"
              items={data.current_month_by_category.map((point) => ({
                label: point.category.name,
                value: point.received,
              }))}
            />
          </div>
          <ZoneMetrics />
        </>
      )}
      <Link
        href="/mapa"
        className="self-start rounded py-3 font-semibold text-primary underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        Ver el mapa
      </Link>
    </main>
  );
}
