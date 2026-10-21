"use client";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  formatReportDate,
  REPORT_PRIORITY_LABELS,
  REPORT_STATUS_LABELS,
  REPORT_STATUS_ORDER,
} from "@/features/reports/presentation";
import { ResolutionEvidenceList } from "@/features/reports/evidence/ResolutionEvidenceList";
import { ReportTimeline } from "@/features/reports/panel/ReportTimeline";
import {
  CitizenPanelApiError,
  getCitizenSummary,
  getMyReports,
  getReportDetail,
  getReportEvidence,
  getReportHistory,
} from "@/lib/api/reports";
import type {
  CitizenSummary,
  MyReportsPage,
  PrivateReportDetail,
  ReportHistoryPage,
  ResolutionEvidence,
} from "@/types/api";

const REPORTS_PER_PAGE = 20;
const REWARD_STATUS_LABELS = {
  PENDIENTE: "Pendiente",
  ACREDITADO: "Acreditado",
  ANULADO: "Anulado",
  REVERTIDO: "Revertido",
} as const;

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
      historyPage: number;
      history: ReportHistoryPage;
      evidence:
        | { phase: "loading" }
        | { phase: "ready"; items: ResolutionEvidence[] }
        | { phase: "error"; message: string };
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

  async function loadTracking(reportId: string, historyPage = 1) {
    const requestKey = `${reportId}:${historyPage}`;
    if (trackingInFlight.current === requestKey) return;
    trackingController.current?.abort();
    const controller = new AbortController();
    const requestId = ++trackingRequestId.current;
    trackingController.current = controller;
    trackingInFlight.current = requestKey;
    setTracking({ phase: "loading", reportId });

    try {
      const [detail, history] = await Promise.all([
        getReportDetail(reportId, controller.signal),
        getReportHistory(reportId, controller.signal, historyPage),
      ]);
      if (controller.signal.aborted || requestId !== trackingRequestId.current) return;
      setTracking({
        phase: "ready",
        reportId,
        detail,
        historyPage,
        history,
        evidence: { phase: "loading" },
      });
      void getReportEvidence(reportId, controller.signal)
        .then((items) => {
          if (controller.signal.aborted || requestId !== trackingRequestId.current) return;
          setTracking((current) =>
            current?.phase === "ready" && current.reportId === reportId
              ? { ...current, evidence: { phase: "ready", items } }
              : current,
          );
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || requestId !== trackingRequestId.current) return;
          setTracking((current) =>
            current?.phase === "ready" && current.reportId === reportId
              ? { ...current, evidence: { phase: "error", message: errorMessage(error) } }
              : current,
          );
        });
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
      <main
        id="contenido-principal"
        tabIndex={-1}
        className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6"
      >
        <h1 className="text-3xl font-bold text-primary">Panel ciudadano</h1>
        <p className="mt-6 text-sm text-muted" role="status" aria-live="polite">
          Cargando tu resumen y tus reportes…
        </p>
      </main>
    );
  }

  if (state.phase === "error") {
    return (
      <main
        id="contenido-principal"
        tabIndex={-1}
        className="mx-auto w-full max-w-6xl px-4 py-12 sm:px-6"
      >
        <h1 className="text-3xl font-bold text-primary">Panel ciudadano</h1>
        <Card as="section" className="mt-6 border border-warning/40 p-6" role="alert">
          <h2 className="text-xl font-bold text-danger-ink">No pudimos cargar el panel</h2>
          <p className="mt-2 text-sm">{state.message}</p>
          <Button
            variant="primary"
            type="button"
            onClick={() => {
              setState({ phase: "loading" });
              setReload((value) => value + 1);
            }}
            className="mt-4"
          >
            Reintentar
          </Button>
        </Card>
      </main>
    );
  }

  const totalReports = REPORT_STATUS_ORDER.reduce(
    (total, status) => total + state.summary.reports_by_status[status],
    0,
  );
  const totalPages = Math.max(1, Math.ceil(state.reports.count / REPORTS_PER_PAGE));

  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-6xl flex-col gap-10 min-w-0 px-4 py-8 sm:px-6"
    >
      <header>
        <p className="text-sm font-semibold text-success-ink">Tu actividad</p>
        <h1 className="text-3xl font-bold text-primary sm:text-4xl">Panel ciudadano</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted">
          Consultá el estado de tus reportes y los puntos de impacto que generaste.
        </p>
      </header>

      <section aria-labelledby="summary-heading">
        <h2 id="summary-heading" className="text-2xl font-bold text-primary">
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

        <h3 className="mt-8 text-lg font-bold text-primary">Reportes por estado</h3>
        <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {REPORT_STATUS_ORDER.map((status) => (
            <li
              key={status}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/20 bg-primary/5 px-4 py-3"
            >
              <span className="text-sm font-medium">{REPORT_STATUS_LABELS[status]}</span>
              <span
                className="font-bold text-primary"
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
              className="text-2xl font-bold text-primary focus:outline-none"
            >
              Mis reportes
            </h2>
            {state.reports.count > 0 && (
              <p className="mt-1 text-sm text-muted">
                Página {pageNumber} de {totalPages} · {state.reports.count} en total
              </p>
            )}
          </div>
          <Link
            href="/reportes/nuevo"
            className="rounded-lg border border-primary px-4 py-2 text-sm font-semibold text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          >
            Crear reporte
          </Link>
        </div>

        {state.reports.count === 0 ? (
          <Card className="mt-5 border border-dashed border-primary/35 p-7 text-center">
            <h3 className="text-lg font-bold text-primary">Todavía no creaste reportes</h3>
            <p className="mt-2 text-sm text-muted">
              Cuando informes una incidencia, vas a poder seguirla desde acá.
            </p>
            <Link
              href="/reportes/nuevo"
              className="mt-4 inline-flex min-h-11 items-center rounded-lg bg-primary px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              Crear mi primer reporte
            </Link>
          </Card>
        ) : (
          <ul
            className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2"
            aria-busy={listPhase === "loading"}
          >
            {state.reports.results.map((report) => {
              const selected = tracking?.reportId === report.id;
              return (
                <Card
                  as="li"
                  key={report.id}
                  className={`rounded-2xl border bg-surface p-5 shadow-sm ${selected ? "border-primary ring-2 ring-primary/20" : "border-border"}`}
                >
                  <article className="flex h-full flex-col">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <h3 className="text-lg font-bold text-primary">{report.category.name}</h3>
                      <span className="rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
                        {REPORT_STATUS_LABELS[report.status]}
                      </span>
                    </div>
                    <p className="mt-3 line-clamp-3 whitespace-pre-wrap text-sm text-muted">
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
                        <dt className="font-semibold text-muted">Fecha</dt>
                        <dd>
                          <time dateTime={report.created_at}>
                            {formatReportDate(report.created_at)}
                          </time>
                        </dd>
                      </div>
                    </dl>
                    <Button
                      variant="primary"
                      type="button"
                      aria-expanded={selected}
                      aria-controls="report-tracking"
                      onClick={() => (selected ? closeTracking() : void loadTracking(report.id))}
                      className="mt-5 self-start text-sm"
                      disabled={tracking?.phase === "loading" && selected}
                    >
                      {selected ? "Cerrar seguimiento" : "Ver seguimiento"}
                    </Button>
                  </article>
                </Card>
              );
            })}
          </ul>
        )}

        <div aria-live="polite" className="mt-4">
          {listPhase === "loading" && <p role="status">Cargando otra página de reportes…</p>}
          {listPhase === "error" && (
            <div role="alert" className="rounded-xl border border-warning/40 p-4">
              <p className="text-sm">{listError}</p>
              <Button
                variant="secondary"
                type="button"
                onClick={() => failedPage !== null && void loadPage(failedPage)}
                className="mt-3"
              >
                Reintentar página
              </Button>
            </div>
          )}
        </div>

        {state.reports.count > 0 && (
          <nav
            aria-label="Páginas de mis reportes"
            className="mt-5 flex flex-wrap items-center justify-between gap-4"
          >
            <Button
              variant="secondary"
              type="button"
              onClick={() => void loadPage(pageNumber - 1)}
              disabled={state.reports.previous === null || listPhase === "loading"}
            >
              Anterior
            </Button>
            <span className="text-sm" aria-live="polite">
              {pageNumber} / {totalPages}
            </span>
            <Button
              variant="secondary"
              type="button"
              onClick={() => void loadPage(pageNumber + 1)}
              disabled={state.reports.next === null || listPhase === "loading"}
            >
              Siguiente
            </Button>
          </nav>
        )}
      </section>

      {tracking !== null && (
        <Card
          as="section"
          id="report-tracking"
          aria-labelledby="tracking-heading"
          className="border border-primary/25 bg-primary/5 p-5 sm:p-7"
        >
          <h2
            id="tracking-heading"
            ref={trackingHeading}
            tabIndex={-1}
            className="text-2xl font-bold text-primary focus:outline-none"
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
              <Button
                variant="primary"
                type="button"
                onClick={() => void loadTracking(tracking.reportId)}
                className="mt-3"
              >
                Reintentar seguimiento
              </Button>
            </div>
          )}
          {tracking.phase === "ready" && (
            <div className="mt-5">
              <div className="rounded-xl bg-surface p-4">
                <h3 className="font-bold text-primary">{tracking.detail.category.name}</h3>
                <p className="mt-1 text-sm">
                  Estado actual: {REPORT_STATUS_LABELS[tracking.detail.status]}
                </p>
                <p className="mt-1 text-sm">Ciclo actual: {tracking.detail.cycle}</p>
                <p className="mt-1 text-sm">
                  Confirmaciones ciudadanas: {tracking.detail.confirmation_count}
                </p>
                {tracking.detail.resolution_bonus ? (
                  <div className="mt-4 rounded-lg bg-success/10 p-3 text-sm">
                    <p className="font-semibold text-success-ink">Bono de resolución</p>
                    <p className="mt-1">
                      +{tracking.detail.resolution_bonus.impact_points} puntos y +
                      {tracking.detail.resolution_bonus.credits} créditos ·{" "}
                      {REWARD_STATUS_LABELS[tracking.detail.resolution_bonus.status]} · Ciclo{" "}
                      {tracking.detail.resolution_bonus.cycle}
                    </p>
                  </div>
                ) : (
                  <p className="mt-4 text-sm text-muted">
                    Este ciclo todavía no tiene bono de resolución.
                  </p>
                )}
              </div>
              <h3 className="mb-4 mt-7 text-lg font-bold text-primary">Línea de tiempo</h3>
              <ReportTimeline entries={tracking.history.results} />
              {(tracking.history.previous || tracking.history.next) && (
                <nav
                  aria-label="Páginas del historial del reporte"
                  className="mt-5 flex flex-wrap items-center justify-between gap-3"
                >
                  <Button
                    variant="secondary"
                    type="button"
                    disabled={tracking.history.previous === null}
                    onClick={() =>
                      void loadTracking(tracking.reportId, Math.max(1, tracking.historyPage - 1))
                    }
                  >
                    Más recientes
                  </Button>
                  <span className="text-sm">Página {tracking.historyPage}</span>
                  <Button
                    variant="secondary"
                    type="button"
                    disabled={tracking.history.next === null}
                    onClick={() => void loadTracking(tracking.reportId, tracking.historyPage + 1)}
                  >
                    Anteriores
                  </Button>
                </nav>
              )}
              <h3 className="mb-4 mt-7 text-lg font-bold text-primary">Evidencia de resolución</h3>
              {tracking.evidence.phase === "loading" && (
                <p role="status">Cargando evidencia de resolución…</p>
              )}
              {tracking.evidence.phase === "error" && (
                <div role="alert" className="rounded-xl bg-surface p-4">
                  <p>{tracking.evidence.message}</p>
                  <Button
                    variant="secondary"
                    type="button"
                    onClick={() => void loadTracking(tracking.reportId, tracking.historyPage)}
                    className="mt-3"
                  >
                    Reintentar evidencia
                  </Button>
                </div>
              )}
              {tracking.evidence.phase === "ready" && (
                <ResolutionEvidenceList
                  evidence={tracking.evidence.items}
                  currentCycle={tracking.detail.cycle}
                />
              )}
            </div>
          )}
        </Card>
      )}
    </main>
  );
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <Card className="border border-border p-5">
      <dt className="text-sm font-medium text-muted">{label}</dt>
      <dd className="mt-1 text-3xl font-bold text-primary">{value}</dd>
    </Card>
  );
}

function ReportFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-semibold text-muted">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
