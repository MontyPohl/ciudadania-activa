import Link from "next/link";

import type { CreateReportResponse } from "@/features/reports/create/types";

export function ReportSuccess({
  result,
  onCreateAnother,
}: {
  result: CreateReportResponse;
  onCreateAnother: () => void;
}) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-12" aria-live="polite">
      <header className="rounded-2xl bg-brand-green/10 p-6">
        <p className="text-sm font-semibold uppercase tracking-wide text-brand-green">
          {humanReportStatus(result.status)}
        </p>
        <h1 className="mt-1 text-2xl font-bold text-brand-purple">Reporte creado correctamente</h1>
        <p className="mt-2 text-sm text-brand-black/75">
          Tu reporte quedó pendiente de revisión institucional.
        </p>
      </header>

      <section className="rounded-2xl border border-brand-black/10 p-5" aria-label="Recompensa">
        <h2 className="font-bold">Recompensa pendiente</h2>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-brand-black/60">Puntos de impacto</dt>
            <dd className="text-xl font-bold text-brand-purple">+{result.reward.impact_points}</dd>
          </div>
          <div>
            <dt className="text-xs text-brand-black/60">Créditos</dt>
            <dd className="text-xl font-bold text-brand-purple">+{result.reward.credits}</dd>
          </div>
        </dl>
        <p className="mt-3 text-sm text-brand-black/70">
          Estado: <strong>{result.reward.status}</strong>. Se acreditan después de la validación
          institucional.
        </p>
      </section>

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <Link
          href={`/reporte/${result.id}`}
          className="rounded-md bg-brand-purple px-4 py-2 text-center text-sm font-semibold text-white"
        >
          Abrir reporte
        </Link>
        <Link
          href="/mapa"
          className="rounded-md border border-brand-purple px-4 py-2 text-center text-sm font-semibold text-brand-purple"
        >
          Ir al mapa
        </Link>
        <button
          type="button"
          onClick={onCreateAnother}
          className="rounded-md border border-brand-green px-4 py-2 text-sm font-semibold text-brand-green"
        >
          Crear otro reporte
        </button>
      </div>
    </main>
  );
}

function humanReportStatus(status: CreateReportResponse["status"]): string {
  return status === "CREADO" ? "Creado" : status;
}
