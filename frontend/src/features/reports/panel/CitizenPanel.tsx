"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  formatReportDate,
  REPORT_PRIORITY_LABELS,
  REPORT_STATUS_LABELS,
  REPORT_STATUS_ORDER,
} from "@/features/reports/presentation";
import { ReportTimeline } from "@/features/reports/panel/ReportTimeline";
import {
  CitizenPanelApiError,
  getCitizenSummary,
  getMyReports,
  getReportDetail,
  getReportHistory,
} from "@/lib/api/reports";
import type {
  CitizenSummary,
  MyReportsPage,
  PrivateReportDetail,
  ReportHistoryEntry,
} from "@/types/api";

const REPORTS_PER_PAGE = 20;

type InitialState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; summary: CitizenSummary; reports: MyReportsPage };

type TrackingState =
  | { phase: "loading"; reportId: string }
  | { phase: "error"; reportId: string; message: string }
  | {
      phase: "ready";
      reportId: string;
      detail: PrivateReportDetail;
      history: ReportHistoryEntry[];
    };

function errorMessage(error: unknown): string {
  return error instanceof CitizenPanelApiError
    ? error.message
    : "No se pudo completar la consulta. Intentá de nuevo.";
}

export function CitizenPanel() {
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<InitialState>({ phase: "loading" });
  const [pageNumber, setPageNumber] = useState(1);
  const [listPhase, setListPhase] = useState<"ready" | "loading" | "error">("ready");
  const [listError, setListError] = useState("");
  const [failedPage, setFailedPage] = useState<number | null>(null);
  const [tracking, setTracking] = useState<TrackingState | null>(null);
  const pageController = useRef<AbortController | null>(null);
  const pageRequestId = useRef(0);
  const pageInFlight = useRef<number | null>(null);
  const trackingController = useRef<AbortController | null>(null);
  const trackingRequestId = useRef(0);
  const trackingInFlight = useRef<string | null>(null);
  const reportsHeading = useRef<HTMLHeadingElement>(null);
  const trackingHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    Promise.all([
      getCitizenSummary(controller.signal),
      getMyReports({ page: 1 }, controller.signal),
    ])
      .then(([summary, reports]) => {
        if (!active) return;
        setPageNumber(1);
        setListPhase("ready");
        setState({ phase: "ready", summary, reports });
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return;
        controller.abort();
        setState({ phase: "error", message: errorMessage(error) });
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [reload]);

  useEffect(
    () => () => {
      pageController.current?.abort();
      trackingController.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (tracking?.phase === "ready" || tracking?.phase === "error") {
      trackingHeading.current?.focus();
    }
  }, [tracking]);

  function closeTracking() {
    trackingController.current?.abort();
    trackingRequestId.current += 1;
    trackingInFlight.current = null;
    setTracking(null);
  }

  async function loadPage(targetPage: number) {
    if (pageInFlight.current === targetPage) return;
    pageController.current?.abort();
    const controller = new AbortController();
    const requestId = ++pageRequestId.current;
    pageController.current = controller;
    pageInFlight.current = targetPage;
    setListPhase("loading");
    setListError("");
    setFailedPage(null);

    try {
      const reports = await getMyReports({ page: targetPage }, controller.signal);
      if (controller.signal.aborted || requestId !== pageRequestId.current) return;
      closeTracking();
      setState((current) => (current.phase === "ready" ? { ...current, reports } : current));
      setPageNumber(targetPage);
      setListPhase("ready");
      queueMicrotask(() => reportsHeading.current?.focus());
    } catch (error) {
      if (controller.signal.aborted || requestId !== pageRequestId.current) return;
      setListError(errorMessage(error));
      setFailedPage(targetPage);
      setListPhase("error");
    } finally {
      if (requestId === pageRequestId.current) pageInFlight.current = null;
    }
  }

  async function loadTracking(reportId: string) {
    if (trackingInFlight.current === reportId) return;
    trackingController.current?.abort();
    const controller = new AbortController();
    const requestId = ++trackingRequestId.current;
    trackingController.current = controller;
    trackingInFlight.current = reportId;
    setTracking({ phase: "loading", reportId });

    try {
      const [detail, history] = await Promise.all([
        getReportDetail(reportId, controller.signal),
        getReportHistory(reportId, controller.signal),
      ]);
      if (controller.signal.aborted || requestId !== trackingRequestId.current) return;
      setTracking({ phase: "ready", reportId, detail, history });
    } catch (error) {
      if (controller.signal.aborted || requestId !== trackingRequestId.current) return;
      controller.abort();
      setTracking({ phase: "error", reportId, message: errorMessage(error) });
    } finally {
      if (requestId === trackingRequestId.current) trackingInFlight.current = null;
    }
  }

  if (state.phase === "loading") {
    return (
      <main className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6">
        <h1 className="text-3xl font-bold text-brand-purple">Panel ciudadano</h1>
        <p className="mt-6 text-sm text-brand-black/70" role="status" aria-live="polite">
          Cargando tu resumen y tus reportes…
        </p>
      </main>
    );
  }

  if (state.phase === "error") {
    return (
      <main className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6">
        <h1 className="text-3xl font-bold text-brand-purple">Panel ciudadano</h1>
        <section className="mt-6 rounded-2xl border border-brand-orange/40 p-6" role="alert">
          <h2 className="text-xl font-bold text-brand-orange">No pudimos cargar el panel</h2>
          <p className="mt-2 text-sm">{state.message}</p>
          <button
            type="button"
            onClick={() => {
              setState({ phase: "loading" });
              setReload((value) => value + 1);
            }}
            className="mt-4 min-h-11 rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
          >
            Reintentar
          </button>
        </section>
      </main>
    );
  }

  const totalReports = REPORT_STATUS_ORDER.reduce(
    (total, status) => total + state.summary.reports_by_status[status],
    0,
  );
  const totalPages = Math.max(1, Math.ceil(state.reports.count / REPORTS_PER_PAGE));

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-10 overflow-x-hidden px-4 py-8 sm:px-6">
      <header>
        <p className="text-sm font-semibold text-brand-green">Tu actividad</p>
        <h1 className="text-3xl font-bold text-brand-purple sm:text-4xl">Panel ciudadano</h1>
        <p className="mt-2 max-w-2xl text-sm text-brand-black/70">
          Consultá el estado de tus reportes y los puntos de impacto que generaste.
        </p>
      </header>

      <section aria-labelledby="summary-heading">
        <h2 id="summary-heading" className="text-2xl font-bold text-brand-purple">
          Resumen
        </h2>
        <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard label="Reportes totales" value={totalReports} />
          <SummaryCard label="Puntos pendientes" value={state.summary.points.pending} />
          <SummaryCard label="Puntos acreditados" value={state.summary.points.accredited} />
          <SummaryCard
            label="Confirmaciones realizadas"
            value={state.summary.confirmations_performed}
          />
        </dl>

        <h3 className="mt-8 text-lg font-bold text-brand-purple">Reportes por estado</h3>
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {REPORT_STATUS_ORDER.map((status) => (
            <li
              key={status}
              className="flex items-center justify-between gap-3 rounded-xl border border-brand-purple/20 bg-brand-purple/5 px-4 py-3"
            >
              <span className="text-sm font-medium">{REPORT_STATUS_LABELS[status]}</span>
              <span
                className="font-bold text-brand-purple"
                aria-label={`${state.summary.reports_by_status[status]} reportes`}
              >
                {state.summary.reports_by_status[status]}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="my-reports-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2
              id="my-reports-heading"
              ref={reportsHeading}
              tabIndex={-1}
              className="text-2xl font-bold text-brand-purple focus:outline-none"
            >
              Mis reportes
            </h2>
            {state.reports.count > 0 && (
              <p className="mt-1 text-sm text-brand-black/65">
                Página {pageNumber} de {totalPages} · {state.reports.count} en total
              </p>
            )}
          </div>
          <Link
            href="/reportes/nuevo"
            className="rounded-lg border border-brand-purple px-4 py-2 text-sm font-semibold text-brand-purple focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
          >
            Crear reporte
          </Link>
        </div>

        {state.reports.count === 0 ? (
          <div className="mt-5 rounded-2xl border border-dashed border-brand-purple/35 p-7 text-center">
            <h3 className="text-lg font-bold text-brand-purple">Todavía no creaste reportes</h3>
            <p className="mt-2 text-sm text-brand-black/70">
              Cuando informes una incidencia, vas a poder seguirla desde acá.
            </p>
            <Link
              href="/reportes/nuevo"
              className="mt-4 inline-flex min-h-11 items-center rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
            >
              Crear mi primer reporte
            </Link>
          </div>
        ) : (
          <ul
            className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2"
            aria-busy={listPhase === "loading"}
          >
            {state.reports.results.map((report) => {
              const selected = tracking?.reportId === report.id;
              return (
                <li
                  key={report.id}
                  className={`rounded-2xl border bg-white p-5 shadow-sm ${selected ? "border-brand-purple ring-2 ring-brand-purple/20" : "border-brand-black/10"}`}
                >
                  <article className="flex h-full flex-col">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <h3 className="text-lg font-bold text-brand-purple">
                        {report.category.name}
                      </h3>
                      <span className="rounded-full bg-brand-purple/10 px-3 py-1 text-xs font-semibold text-brand-purple">
                        {REPORT_STATUS_LABELS[report.status]}
                      </span>
                    </div>
                    <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm text-brand-black/80">
                      {report.description}
                    </p>
                    <dl className="mt-4 grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                      <ReportFact
                        label="Prioridad"
                        value={REPORT_PRIORITY_LABELS[report.priority]}
                      />
                      <ReportFact
                        label="Institución"
                        value={report.institution?.name ?? "Sin institución asignada"}
                      />
                      <div className="sm:col-span-2">
                        <dt className="font-semibold text-brand-black/65">Fecha</dt>
                        <dd>
                          <time dateTime={report.created_at}>
                            {formatReportDate(report.created_at)}
                          </time>
                        </dd>
                      </div>
                    </dl>
                    <button
                      type="button"
                      aria-expanded={selected}
                      aria-controls="report-tracking"
                      onClick={() => (selected ? closeTracking() : void loadTracking(report.id))}
                      className="mt-5 min-h-11 self-start rounded-lg bg-brand-purple px-4 py-2 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={tracking?.phase === "loading" && selected}
                    >
                      {selected ? "Cerrar seguimiento" : "Ver seguimiento"}
                    </button>
                  </article>
                </li>
              );
            })}
          </ul>
        )}

        <div aria-live="polite" className="mt-4">
          {listPhase === "loading" && <p role="status">Cargando otra página de reportes…</p>}
          {listPhase === "error" && (
            <div role="alert" className="rounded-xl border border-brand-orange/40 p-4">
              <p className="text-sm">{listError}</p>
              <button
                type="button"
                onClick={() => failedPage !== null && void loadPage(failedPage)}
                className="mt-3 min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
              >
                Reintentar página
              </button>
            </div>
          )}
        </div>

        {state.reports.count > 0 && (
          <nav
            aria-label="Páginas de mis reportes"
            className="mt-5 flex items-center justify-between gap-4"
          >
            <button
              type="button"
              onClick={() => void loadPage(pageNumber - 1)}
              disabled={state.reports.previous === null || listPhase === "loading"}
              className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple disabled:cursor-not-allowed disabled:opacity-45"
            >
              Anterior
            </button>
            <span className="text-sm" aria-live="polite">
              {pageNumber} / {totalPages}
            </span>
            <button
              type="button"
              onClick={() => void loadPage(pageNumber + 1)}
              disabled={state.reports.next === null || listPhase === "loading"}
              className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple disabled:cursor-not-allowed disabled:opacity-45"
            >
              Siguiente
            </button>
          </nav>
        )}
      </section>

      {tracking !== null && (
        <section
          id="report-tracking"
          aria-labelledby="tracking-heading"
          className="rounded-2xl border border-brand-purple/25 bg-brand-purple/5 p-5 sm:p-7"
        >
          <h2
            id="tracking-heading"
            ref={trackingHeading}
            tabIndex={-1}
            className="text-2xl font-bold text-brand-purple focus:outline-none"
          >
            Seguimiento del reporte
          </h2>
          {tracking.phase === "loading" && (
            <p className="mt-4" role="status" aria-live="polite">
              Cargando detalle e historial…
            </p>
          )}
          {tracking.phase === "error" && (
            <div className="mt-4" role="alert">
              <p>{tracking.message}</p>
              <button
                type="button"
                onClick={() => void loadTracking(tracking.reportId)}
                className="mt-3 min-h-11 rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
              >
                Reintentar seguimiento
              </button>
            </div>
          )}
          {tracking.phase === "ready" && (
            <div className="mt-5">
              <div className="rounded-xl bg-white p-4">
                <h3 className="font-bold text-brand-purple">{tracking.detail.category.name}</h3>
                <p className="mt-1 text-sm">
                  Estado actual: {REPORT_STATUS_LABELS[tracking.detail.status]}
                </p>
                <p className="mt-1 text-sm">
                  Confirmaciones ciudadanas: {tracking.detail.confirmation_count}
                </p>
              </div>
              <h3 className="mb-4 mt-7 text-lg font-bold text-brand-purple">Línea de tiempo</h3>
              <ReportTimeline entries={tracking.history} />
            </div>
          )}
        </section>
      )}
    </main>
  );
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-2xl border border-brand-black/10 bg-white p-5 shadow-sm">
      <dt className="text-sm font-medium text-brand-black/65">{label}</dt>
      <dd className="mt-1 text-3xl font-bold text-brand-purple">{value}</dd>
    </div>
  );
}

function ReportFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-semibold text-brand-black/65">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
