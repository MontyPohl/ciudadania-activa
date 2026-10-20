import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
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
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-12"
      aria-live="polite"
    >
      <header className="rounded-2xl bg-success/10 p-6">
        <p className="text-sm font-semibold uppercase tracking-wide text-success-ink">
          {humanReportStatus(result.status)}
        </p>
        <h1 className="mt-1 text-2xl font-bold text-primary">Reporte creado correctamente</h1>
        <p className="mt-2 text-sm text-muted">
          Tu reporte quedó pendiente de revisión institucional.
        </p>
      </header>

      <Card as="section" className="border border-border p-5" aria-label="Recompensa">
        <h2 className="font-bold">Recompensa pendiente</h2>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-muted">Puntos de impacto</dt>
            <dd className="text-xl font-bold text-primary">+{result.reward.impact_points}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Créditos</dt>
            <dd className="text-xl font-bold text-primary">+{result.reward.credits}</dd>
          </div>
        </dl>
        <p className="mt-3 text-sm text-muted">
          Estado: <strong>{result.reward.status}</strong>. Se acreditan después de la validación
          institucional.
        </p>
      </Card>

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <Link
          href={`/reporte/${result.id}`}
          className="rounded-md bg-primary px-4 py-2 text-center text-sm font-semibold text-white"
        >
          Abrir reporte
        </Link>
        <Link
          href="/mapa"
          className="rounded-md border border-primary px-4 py-2 text-center text-sm font-semibold text-primary"
        >
          Ir al mapa
        </Link>
        <Button variant="secondary" type="button" onClick={onCreateAnother} className="text-sm">
          Crear otro reporte
        </Button>
      </div>
    </main>
  );
}

function humanReportStatus(status: CreateReportResponse["status"]): string {
  return status === "CREADO" ? "Creado" : status;
}
