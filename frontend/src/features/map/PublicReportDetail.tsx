"use client";

import dynamic from "next/dynamic";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { PublicPhoto, publicMediaUrl } from "./PublicPhoto";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  normalizeCategory,
  normalizeReportId,
  normalizeStateFilter,
} from "@/features/map/queryParams";
import { ReportFields } from "@/features/map/ReportFields";
import { ReportConfirmation } from "@/features/reports/confirmation/ReportConfirmation";
import { getPublicReport, PublicApiError } from "@/lib/api/publicReports";
import type { PublicReportDetailData } from "@/types/api";

const ReportMiniMap = dynamic(() => import("./ReportMiniMap"), {
  ssr: false,
  loading: () => <p role="status">Cargando ubicación…</p>,
});

type DetailState =
  | { phase: "loading" }
  | { phase: "ready"; report: PublicReportDetailData }
  | { phase: "not-found" }
  | { phase: "rate-limited" }
  | { phase: "error" };

export function PublicReportDetail({ id }: { id: string }) {
  const searchParams = useSearchParams();
  const validId = normalizeReportId(id) !== null;
  const [state, setState] = useState<DetailState>(
    validId ? { phase: "loading" } : { phase: "not-found" },
  );

  const backHref = useMemo(() => {
    const params = new URLSearchParams();
    const stateFilter = normalizeStateFilter(searchParams.get("estado"));
    const category = normalizeCategory(searchParams.get("categoria"));
    const selected = normalizeReportId(searchParams.get("reporte"));
    if (stateFilter !== "active") params.set("estado", stateFilter);
    if (category !== null) params.set("categoria", category);
    if (selected === id) params.set("reporte", id);
    const query = params.toString();
    return query === "" ? "/mapa" : `/mapa?${query}`;
  }, [id, searchParams]);

  const updateConfirmationCount = useCallback((confirmationCount: number) => {
    setState((current) =>
      current.phase === "ready"
        ? { ...current, report: { ...current.report, confirmation_count: confirmationCount } }
        : current,
    );
  }, []);

  useEffect(() => {
    if (!validId) return;

    const controller = new AbortController();
    let active = true;
    queueMicrotask(() => {
      if (active) setState({ phase: "loading" });
    });
    getPublicReport(id, controller.signal)
      .then((report) => {
        if (active) setState({ phase: "ready", report });
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return;
        if (error instanceof PublicApiError && error.status === 404) {
          setState({ phase: "not-found" });
        } else if (error instanceof PublicApiError && error.status === 429) {
          setState({ phase: "rate-limited" });
        } else {
          setState({ phase: "error" });
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [id, validId]);

  const renderedState: DetailState = validId ? state : { phase: "not-found" };

  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-6 py-10"
    >
      <Link href={backHref} className="text-sm font-semibold text-primary underline">
        ← Volver al mapa
      </Link>

      {renderedState.phase === "loading" && <p role="status">Cargando el reporte…</p>}

      {renderedState.phase === "not-found" && (
        <section role="alert" className="rounded-2xl border border-black/10 p-6">
          <h1 className="text-2xl font-bold text-primary">Reporte no encontrado</h1>
          <p className="mt-2 text-sm text-muted">
            El reporte no existe o no forma parte de la información pública.
          </p>
        </section>
      )}

      {renderedState.phase === "rate-limited" && (
        <section role="alert" className="rounded-2xl border border-brand-orange/30 p-6">
          <h1 className="text-2xl font-bold text-brand-orange-ink">Demasiadas consultas</h1>
          <p className="mt-2 text-sm">Esperá un momento y volvé a intentarlo.</p>
        </section>
      )}

      {renderedState.phase === "error" && (
        <section role="alert" className="rounded-2xl border border-brand-orange/30 p-6">
          <h1 className="text-2xl font-bold text-brand-orange-ink">No se pudo cargar el reporte</h1>
          <p className="mt-2 text-sm">Intentá nuevamente dentro de unos minutos.</p>
        </section>
      )}

      {renderedState.phase === "ready" && (
        <div className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Card as="article">
            <p className="text-sm font-semibold text-brand-green-ink">Reporte público</p>
            <h1 className="mb-6 text-3xl font-bold text-primary">
              {renderedState.report.category.name}
            </h1>
            <div className="mb-4 flex flex-wrap gap-2">
              <Badge label={renderedState.report.category.name} />
              <Badge status={renderedState.report.status} />
            </div>
            <PublicPhoto report={renderedState.report} enlarge />
            <ReportFields report={renderedState.report} />
            <section aria-label="Progreso del reporte" className="my-6 rounded-xl bg-canvas p-4">
              <h2 className="mb-3 font-semibold">Progreso del estado</h2>
              <p className="mb-3 text-sm text-muted">
                Derivado del estado actual, sin fechas de transición.
              </p>
              <ol className="grid grid-cols-3 gap-2 text-center text-xs sm:text-sm">
                {["Creado", "En proceso", "Resuelto"].map((label, i) => {
                  const current = ["RESUELTO", "CERRADO"].includes(renderedState.report.status)
                    ? 2
                    : renderedState.report.status === "EN_PROCESO"
                      ? 1
                      : 0;
                  return (
                    <li
                      key={label}
                      aria-current={i === current ? "step" : undefined}
                      className={
                        i <= current
                          ? "border-t-4 border-primary pt-3 font-semibold text-primary"
                          : "border-t-4 border-border pt-3 text-muted"
                      }
                    >
                      {label}
                    </li>
                  );
                })}
              </ol>
            </section>
            <section aria-label="Evidencia de resolución" className="my-6 space-y-3">
              <h2 className="text-lg font-semibold">Evidencia de resolución</h2>
              {renderedState.report.resolution_evidences.length === 0 ? (
                <p className="text-sm text-muted">
                  No hay evidencia de resolución pública disponible.
                </p>
              ) : (
                renderedState.report.resolution_evidences.map((evidence) => (
                  <figure key={evidence.id}>
                    <a
                      href={publicMediaUrl(evidence.evidence_url)}
                      target="_blank"
                      rel="noreferrer"
                      className="block rounded-lg focus-visible:outline-2 focus-visible:outline-focus"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element -- Public proxy media. */}
                      <img
                        src={publicMediaUrl(evidence.evidence_url)}
                        loading="lazy"
                        referrerPolicy="no-referrer"
                        alt={`Evidencia de resolución de ${renderedState.report.category.name} · ${renderedState.report.status === "CERRADO" ? "Cerrado" : "Resuelto"}`}
                        className="max-h-80 w-full rounded-xl object-contain"
                      />
                      <span className="text-sm text-primary underline">
                        Ampliar evidencia (nueva pestaña)
                      </span>
                    </a>
                  </figure>
                ))
              )}
            </section>
            <ReportConfirmation
              reportId={renderedState.report.id}
              onConfirmationCountChange={updateConfirmationCount}
            />
          </Card>
          <Card as="section" className="self-start space-y-4">
            <h2 className="text-lg font-semibold">Ubicación aproximada</h2>
            <ReportMiniMap report={renderedState.report} />
            <p className="text-sm text-muted">
              {renderedState.report.institution?.name ?? "Sin institución asignada"}
            </p>
          </Card>
        </div>
      )}
    </main>
  );
}
