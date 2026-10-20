"use client";
import { useCallback, useState } from "react";
import type { SessionInstitution } from "@/types/api";
import { MetricsQuery } from "./MetricsQuery";
import { metricsPageClass } from "./MetricsFeedback";
import { PeriodSelect } from "./PeriodSelect";
import { currentPeriod } from "./metricsLabels";
export function AdminMetricsPage() {
  const [period, setPeriod] = useState(currentPeriod);
  const [institutionId, setInstitutionId] = useState("");
  const [institutions, setInstitutions] = useState<SessionInstitution[] | null>(null);
  const rememberInstitutions = useCallback(
    (options: SessionInstitution[]) => setInstitutions((previous) => previous ?? options),
    [],
  );
  return (
    <main className={metricsPageClass}>
      <header>
        <h1 className="text-3xl font-bold text-brand-purple">Métricas de atención</h1>
        <p className="mt-2 max-w-2xl text-brand-black/70">
          Consultá el consolidado o elegí una institución para ver su actividad.
        </p>
      </header>
      <div className="grid items-start gap-4 rounded-2xl bg-brand-purple/5 p-4 sm:grid-cols-2 sm:p-6">
        <label className="flex max-w-md flex-col gap-1 text-sm font-medium">
          Institución
          <select
            className="audit-input"
            value={institutionId}
            onChange={(e) => setInstitutionId(e.target.value)}
          >
            <option value="">Todas las instituciones</option>
            {institutions?.map((institution) => (
              <option key={institution.id} value={institution.id}>
                {institution.name}
              </option>
            ))}
          </select>
        </label>
        <PeriodSelect value={period} onChange={setPeriod} />
      </div>
      <MetricsQuery
        key={`${institutionId}-${period}`}
        role="admin"
        institutionId={institutionId || undefined}
        period={period}
        onInstitutions={rememberInstitutions}
      />
    </main>
  );
}
