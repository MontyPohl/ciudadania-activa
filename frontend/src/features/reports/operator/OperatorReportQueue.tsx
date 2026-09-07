"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useSession } from "@/features/auth/useSession";
import { OperatorInstitutionSelector } from "@/features/reports/operator/OperatorInstitutionSelector";
import {
  REPORT_PRIORITY_LABELS,
  REPORT_STATUS_LABELS,
  formatReportDate,
} from "@/features/reports/presentation";
import {
  getOperatorReports,
  OperatorApiError,
  operatorErrorMessage,
} from "@/lib/api/operatorReports";
import type { OperatorReport, PaginatedResponse } from "@/types/api";

type View =
  | { phase: "loading" }
  | { phase: "ready"; page: PaginatedResponse<OperatorReport> }
  | { phase: "error"; message: string };

export function OperatorReportQueue() {
  const session = useSession();
  const institutions = session.status === "authenticated" ? session.user.institutions : [];
  const [selectedInstitutionId, setSelectedInstitutionId] = useState("");
  const institutionId = selectedInstitutionId || institutions[0]?.id || "";
  const [view, setView] = useState<View>({ phase: "loading" });

  useEffect(() => {
    if (!institutionId) return;
    let cancelled = false;
    getOperatorReports(institutionId)
      .then((page) => {
        if (!cancelled) setView({ phase: "ready", page });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof OperatorApiError ? error.message : operatorErrorMessage("SERVER_ERROR");
        setView({ phase: "error", message });
      });
    return () => {
      cancelled = true;
    };
  }, [institutionId]);

  if (institutions.length === 0) {
    return <p role="alert">No tenés membresías institucionales activas.</p>;
  }

  return (
    <section className="flex flex-col gap-5" aria-labelledby="operator-queue-title">
      <h2 id="operator-queue-title" className="sr-only">
        Reportes operables
      </h2>
      <OperatorInstitutionSelector
        institutions={institutions}
        value={institutionId}
        onChange={(value) => {
          setView({ phase: "loading" });
          setSelectedInstitutionId(value);
        }}
      />

      {view.phase === "loading" && <p role="status">Cargando reportes…</p>}
      {view.phase === "error" && <p role="alert">{view.message}</p>}
      {view.phase === "ready" && view.page.count === 0 && (
        <p>No hay reportes en esta bandeja institucional.</p>
      )}
      {view.phase === "ready" && view.page.count > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[42rem] border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-brand-black/10">
                <th className="py-2 pr-4">Reporte</th>
                <th className="py-2 pr-4">Estado</th>
                <th className="py-2 pr-4">Prioridad</th>
                <th className="py-2 pr-4">Institución efectiva</th>
                <th className="py-2">Acción</th>
              </tr>
            </thead>
            <tbody>
              {view.page.results.map((report) => (
                <tr key={report.id} className="border-b border-brand-black/5">
                  <td className="py-3 pr-4">
                    <span className="block font-medium">{report.category.name}</span>
                    <span className="text-xs text-brand-black/60">
                      {formatReportDate(report.created_at)}
                    </span>
                  </td>
                  <td className="py-3 pr-4">{REPORT_STATUS_LABELS[report.status]}</td>
                  <td className="py-3 pr-4">{REPORT_PRIORITY_LABELS[report.priority]}</td>
                  <td className="py-3 pr-4">{report.effective_read_institution.name}</td>
                  <td className="py-3">
                    <Link
                      href={`/bandeja/${report.id}?institution_id=${encodeURIComponent(institutionId)}`}
                      className="font-medium text-brand-purple underline"
                    >
                      Abrir detalle
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
