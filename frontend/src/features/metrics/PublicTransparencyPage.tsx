"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getPublicMetrics } from "@/lib/api/publicMetrics";
import { PublicApiError } from "@/lib/api/publicReports";
import type {
  PublicMetrics,
  PublicMetricsCells,
  PublicInstitutionMetrics,
  PublicSuppressible,
} from "@/types/api";
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
function InstitutionCard({ cells }: { cells: PublicInstitutionMetrics }) {
  return (
    <section className="space-y-4 rounded-2xl border border-brand-black/10 p-4 sm:p-6">
      <h2 className="text-xl font-semibold">{cells.institution.name}</h2>
      <PublicCells cells={cells} />
      <dl>
        <KpiTile
          label="Cumplimiento de SLA"
          value={cells.sla.compliance_rate === null ? null : formatRate(cells.sla.compliance_rate)}
          nullHelp={
            cells.suppressed.includes("sla.compliance_rate")
              ? suppressedHelp
              : !cells.sla.configured
                ? "Sin SLA definido"
                : "Todavía no hay casos para calcular este dato."
          }
          help={
            cells.sla.hours === null
              ? undefined
              : `Plazo de atención: ${formatHours(cells.sla.hours)}.`
          }
        />
      </dl>
    </section>
  );
}
export function PublicTransparencyPage() {
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
    <main className={metricsPageClass}>
      <header>
        <p className="text-sm font-semibold text-brand-purple">
          Ciudadanía Activa · datos abiertos
        </p>
        <h1 className="mt-1 text-3xl font-bold text-brand-purple">
          Transparencia: últimos 6 meses
        </h1>
        {data && (
          <p className="mt-2 text-brand-black/70">
            Del {longDate(data.window.from)} al {longDate(data.window.to)}.
          </p>
        )}
      </header>
      <p className="rounded-2xl bg-brand-purple/5 p-4 text-sm leading-relaxed">
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
          {data.totals.received === 0 ? (
            <p role="status" className="rounded-2xl bg-brand-purple/5 p-6">
              Todavía no hay reportes en estos 6 meses.
            </p>
          ) : (
            data.institutions.map((cells) => (
              <InstitutionCard key={cells.institution.id} cells={cells} />
            ))
          )}
        </>
      )}
      <Link
        href="/mapa"
        className="self-start rounded py-3 font-semibold text-brand-purple underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
      >
        Ver el mapa
      </Link>
    </main>
  );
}
