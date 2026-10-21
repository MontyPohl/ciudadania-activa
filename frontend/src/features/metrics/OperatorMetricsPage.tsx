"use client";
import { useState } from "react";
import { useSession } from "@/features/auth/useSession";
import { OperatorInstitutionSelector } from "@/features/reports/operator/OperatorInstitutionSelector";
import { MetricsQuery } from "./MetricsQuery";
import { MetricsLoading, metricsPageClass } from "./MetricsFeedback";
import { PeriodSelect } from "./PeriodSelect";
import { currentPeriod } from "./metricsLabels";
export function OperatorMetricsPage() {
  const session = useSession();
  const institutions = session.status === "authenticated" ? session.user.institutions : [];
  const [selected, setSelected] = useState("");
  const [period, setPeriod] = useState(currentPeriod);
  const institutionId = selected || institutions[0]?.id || "";
  return (
    <main id="contenido-principal" tabIndex={-1} className={metricsPageClass}>
      <header>
        <h1 className="text-3xl font-bold text-primary">Métricas de tu institución</h1>
        <p className="mt-2 max-w-2xl text-muted">
          Consultá la atención de reportes y los incentivos de cada mes.
        </p>
      </header>
      {session.status === "loading" ? (
        <MetricsLoading />
      ) : !institutions.length ? (
        <p role="alert">No tenés membresías institucionales activas.</p>
      ) : (
        <>
          <div className="grid items-start gap-4 rounded-2xl bg-primary/5 p-4 sm:grid-cols-2 sm:p-6">
            <OperatorInstitutionSelector
              institutions={institutions}
              value={institutionId}
              onChange={setSelected}
            />
            <PeriodSelect value={period} onChange={setPeriod} />
          </div>
          <MetricsQuery
            key={`${institutionId}-${period}`}
            role="operator"
            institutionId={institutionId}
            period={period}
          />
        </>
      )}
    </main>
  );
}
