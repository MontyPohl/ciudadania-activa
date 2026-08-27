"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import {
  normalizeCategory,
  normalizeReportId,
  normalizeStateFilter,
} from "@/features/map/queryParams";
import { ReportFields } from "@/features/map/ReportFields";
import { getPublicReport, PublicApiError } from "@/lib/api/publicReports";
import type { PublicReport } from "@/types/api";

type DetailState =
  | { phase: "loading" }
  | { phase: "ready"; report: PublicReport }
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
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-6 py-10">
      <Link href={backHref} className="text-sm font-semibold text-brand-purple underline">
        ← Volver al mapa
      </Link>

      {renderedState.phase === "loading" && <p role="status">Cargando el reporte…</p>}

      {renderedState.phase === "not-found" && (
        <section role="alert" className="rounded-2xl border border-black/10 p-6">
          <h1 className="text-2xl font-bold text-brand-purple">Reporte no encontrado</h1>
          <p className="mt-2 text-sm text-black/70">
            El reporte no existe o no forma parte de la información pública.
          </p>
        </section>
      )}

      {renderedState.phase === "rate-limited" && (
        <section role="alert" className="rounded-2xl border border-brand-orange/30 p-6">
          <h1 className="text-2xl font-bold text-brand-orange">Demasiadas consultas</h1>
          <p className="mt-2 text-sm">Esperá un momento y volvé a intentarlo.</p>
        </section>
      )}

      {renderedState.phase === "error" && (
        <section role="alert" className="rounded-2xl border border-brand-orange/30 p-6">
          <h1 className="text-2xl font-bold text-brand-orange">No se pudo cargar el reporte</h1>
          <p className="mt-2 text-sm">Intentá nuevamente dentro de unos minutos.</p>
        </section>
      )}

      {renderedState.phase === "ready" && (
        <article className="rounded-2xl border border-black/10 bg-white p-6 shadow-sm">
          <p className="text-sm font-semibold text-brand-green">Reporte público</p>
          <h1 className="mb-6 text-3xl font-bold text-brand-purple">
            {renderedState.report.category.name}
          </h1>
          <ReportFields report={renderedState.report} />
        </article>
      )}
    </main>
  );
}
