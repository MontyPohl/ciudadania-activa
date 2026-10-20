"use client";

import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
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
  const [pageNumber, setPageNumber] = useState(1);
  const [reloadToken, setReloadToken] = useState(0);
  const [view, setView] = useState<View>({ phase: "loading" });

  useEffect(() => {
    if (!institutionId) return;
    let cancelled = false;
    getOperatorReports(institutionId, pageNumber)
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
  }, [institutionId, pageNumber, reloadToken]);

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
          setPageNumber(1);
          setSelectedInstitutionId(value);
        }}
      />

      {view.phase === "loading" && <p role="status">Cargando reportes…</p>}
      {view.phase === "error" && (
        <div className="flex items-center gap-3">
          <p role="alert">{view.message}</p>
          <Button
            variant="secondary"
            type="button"
            className="underline"
            onClick={() => {
              setView({ phase: "loading" });
              setReloadToken((value) => value + 1);
            }}
          >
            Reintentar
          </Button>
        </div>
      )}
      {view.phase === "ready" && view.page.count === 0 && (
        <p>No hay reportes en esta bandeja institucional.</p>
      )}
      {view.phase === "ready" && view.page.count > 0 && (
        <div className="flex flex-col gap-4">
          <Card
            tabIndex={0}
            className="max-w-full overflow-x-auto px-4 ring-1 ring-border focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
          >
            <table className="w-full min-w-[42rem] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="py-2 pr-4">Reporte</th>
                  <th className="py-2 pr-4">Estado</th>
                  <th className="py-2 pr-4">Prioridad</th>
                  <th className="py-2 pr-4">Institución efectiva</th>
                  <th className="py-2">Acción</th>
                </tr>
              </thead>
              <tbody>
                {view.page.results.map((report) => (
                  <tr key={report.id} className="border-b border-border">
                    <td className="py-3 pr-4">
                      <span className="block font-medium">{report.category.name}</span>
                      <span className="text-xs text-muted">
                        {formatReportDate(report.created_at)}
                      </span>
                    </td>
                    <td className="py-3 pr-4">
                      <span className="block">{REPORT_STATUS_LABELS[report.status]}</span>
                      {report.cycle > 0 && (
                        <span className="text-xs text-muted">Ciclo {report.cycle}</span>
                      )}
                    </td>
                    <td className="py-3 pr-4">{REPORT_PRIORITY_LABELS[report.priority]}</td>
                    <td className="py-3 pr-4">{report.effective_read_institution.name}</td>
                    <td className="py-3">
                      <Link
                        href={`/bandeja/${report.id}?institution_id=${encodeURIComponent(institutionId)}`}
                        className="inline-flex min-h-11 items-center font-medium text-primary underline"
                      >
                        Abrir detalle
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <nav
            className="flex flex-wrap items-center justify-between gap-3"
            aria-label="Paginación de reportes"
          >
            <Button
              variant="secondary"
              type="button"
              disabled={view.page.previous === null}
              onClick={() => {
                setView({ phase: "loading" });
                setPageNumber((value) => Math.max(1, value - 1));
              }}
              className="underline"
            >
              Página anterior
            </Button>
            <span className="text-sm">Página {pageNumber}</span>
            <Button
              variant="secondary"
              type="button"
              disabled={view.page.next === null}
              onClick={() => {
                setView({ phase: "loading" });
                setPageNumber((value) => value + 1);
              }}
              className="underline"
            >
              Página siguiente
            </Button>
          </nav>
        </div>
      )}
    </section>
  );
}
