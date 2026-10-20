"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isQueryableBbox, serializeBbox, type MapBbox } from "@/features/map/bbox";
import { MAP_DEBOUNCE_MS } from "@/features/map/mapConfig";
import { MapFilters } from "@/features/map/MapFilters";
import { MapLoading } from "@/features/map/MapLoading";
import { MapResultsStatus, type MapResultsPhase } from "@/features/map/MapResultsStatus";
import type { MapFocusTarget } from "@/features/map/MapViewportEvents";
import { parseMapQuery, updateMapQuery } from "@/features/map/queryParams";
import { ReportList } from "@/features/map/ReportList";
import { ReportSidePanel } from "@/features/map/ReportSidePanel";
import {
  getPublicCategories,
  getPublicReport,
  getPublicReports,
  PublicApiError,
} from "@/lib/api/publicReports";
import type { PublicCategory, PublicReport, PublicReportStateFilter } from "@/types/api";

const MapView = dynamic(() => import("@/features/map/MapView"), {
  ssr: false,
  loading: () => <MapLoading />,
});

type CategoriesState =
  | { phase: "loading"; items: PublicCategory[] }
  | { phase: "ready"; items: PublicCategory[] }
  | { phase: "error"; items: PublicCategory[] };

interface Viewport {
  bbox: MapBbox;
  zoom: number;
}

interface FocusOrigin {
  reportId: string;
  node: HTMLElement | null;
}

export function MapPageClient() {
  const [mobileView, setMobileView] = useState<"list" | "map">("list");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const query = parseMapQuery(searchParams);
  const [categories, setCategories] = useState<CategoriesState>({ phase: "loading", items: [] });
  const [viewport, setViewport] = useState<Viewport | null>(null);
  const [reports, setReports] = useState<PublicReport[]>([]);
  const [resultsPhase, setResultsPhase] = useState<MapResultsPhase>("idle");
  const [deepLinkReport, setDeepLinkReport] = useState<PublicReport | null>(null);
  const [deepLinkFocus, setDeepLinkFocus] = useState<MapFocusTarget | null>(null);
  const [deepLinkMessage, setDeepLinkMessage] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const displayedResultsKey = useRef<string | null>(null);
  const attemptedDeepLink = useRef<string | null>(null);
  const localSelection = useRef<string | null>(null);
  const focusOrigin = useRef<FocusOrigin | null>(null);
  const pendingFocusReportId = useRef<string | null>(null);
  const pendingFocusOriginalNode = useRef<HTMLElement | null>(null);
  const pendingFocusSawReload = useRef(false);
  const reportTriggerNodes = useRef(new Map<string, HTMLButtonElement>());
  const reportListRef = useRef<HTMLElement | null>(null);
  const resultsStatusRef = useRef<HTMLDivElement | null>(null);
  const queryReportRef = useRef(query.report);

  const clearPendingFocus = useCallback(() => {
    pendingFocusReportId.current = null;
    pendingFocusOriginalNode.current = null;
    pendingFocusSawReload.current = false;
  }, []);

  const handleReportTriggerRef = useCallback(
    (reportId: string, node: HTMLButtonElement | null) => {
      if (node === null) {
        reportTriggerNodes.current.delete(reportId);
        return;
      }

      reportTriggerNodes.current.set(reportId, node);
      if (
        pendingFocusReportId.current === reportId &&
        queryReportRef.current === null &&
        node !== pendingFocusOriginalNode.current
      ) {
        node.focus();
        clearPendingFocus();
      }
    },
    [clearPendingFocus],
  );

  const replaceQuery = useCallback(
    (changes: Parameters<typeof updateMapQuery>[1]) => {
      const next = updateMapQuery(searchParams.toString(), changes);
      router.replace(`${pathname}${next}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  useEffect(() => {
    queryReportRef.current = query.report;
    if (query.report === null) {
      attemptedDeepLink.current = null;
      localSelection.current = null;
    }
  }, [query.report]);

  useEffect(() => {
    const rawState = searchParams.get("estado");
    const rawReport = searchParams.get("reporte");
    const stateInvalid = rawState !== null && !["active", "resolved", "all"].includes(rawState);
    const reportInvalid = rawReport !== null && query.report === null;
    if (stateInvalid || reportInvalid) {
      replaceQuery({
        ...(stateInvalid ? { estado: "active" as const } : {}),
        ...(reportInvalid ? { reporte: null } : {}),
      });
    }
  }, [query.report, replaceQuery, searchParams]);

  useEffect(() => {
    const controller = new AbortController();
    getPublicCategories(controller.signal)
      .then((items) => setCategories({ phase: "ready", items }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setCategories({ phase: "error", items: [] });
      });
    return () => controller.abort();
  }, []);

  const categoryIsKnown =
    query.category === null || categories.items.some((item) => item.slug === query.category);

  useEffect(() => {
    if (query.category === null || categories.phase === "loading") return;
    if (!categoryIsKnown) replaceQuery({ categoria: null });
  }, [categories.phase, categoryIsKnown, query.category, replaceQuery]);

  const viewportIsQueryable = viewport !== null && isQueryableBbox(viewport.bbox);
  const serializedViewport = viewportIsQueryable ? serializeBbox(viewport.bbox) : null;
  const hasViewport = viewport !== null;
  const effectiveCategory = categoryIsKnown ? query.category : null;

  useEffect(() => {
    if (!hasViewport) return;

    let disposed = false;
    const updateResultsSoon = (nextReports: PublicReport[], phase: MapResultsPhase) => {
      queueMicrotask(() => {
        if (disposed) return;
        setReports(nextReports);
        setResultsPhase(phase);
      });
    };

    requestSequence.current += 1;
    const sequence = requestSequence.current;

    if (!viewportIsQueryable) {
      displayedResultsKey.current = null;
      updateResultsSoon([], "too-wide");
      return () => {
        disposed = true;
      };
    }

    if (serializedViewport === null) return;

    if (query.category !== null && categories.phase === "loading") {
      displayedResultsKey.current = null;
      updateResultsSoon([], "idle");
      return () => {
        disposed = true;
      };
    }

    if (query.category !== null && !categoryIsKnown) {
      displayedResultsKey.current = null;
      updateResultsSoon([], "idle");
      return () => {
        disposed = true;
      };
    }

    const key = `${serializedViewport}|${query.stateFilter}|${effectiveCategory ?? ""}`;
    if (displayedResultsKey.current === key) return;

    const controller = new AbortController();
    displayedResultsKey.current = null;
    updateResultsSoon([], "loading");

    const timer = window.setTimeout(() => {
      getPublicReports(
        {
          bbox: serializedViewport,
          stateFilter: query.stateFilter,
          category: effectiveCategory,
        },
        controller.signal,
      )
        .then((page) => {
          if (disposed || sequence !== requestSequence.current) return;
          displayedResultsKey.current = key;
          setReports(page.results);
          setResultsPhase(page.results.length === 0 ? "empty" : "ready");
        })
        .catch((error: unknown) => {
          if (disposed || controller.signal.aborted || sequence !== requestSequence.current) return;
          setReports([]);
          setResultsPhase(
            error instanceof PublicApiError && error.status === 429 ? "rate-limited" : "error",
          );
        });
    }, MAP_DEBOUNCE_MS);

    return () => {
      disposed = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    categories.phase,
    categoryIsKnown,
    effectiveCategory,
    hasViewport,
    query.category,
    query.stateFilter,
    serializedViewport,
    viewportIsQueryable,
  ]);

  const reportFromResults = useMemo(
    () => reports.find((report) => report.id === query.report) ?? null,
    [query.report, reports],
  );
  const activeDeepLinkReport = deepLinkReport?.id === query.report ? deepLinkReport : null;
  const selectedReport = reportFromResults ?? activeDeepLinkReport;

  useEffect(() => {
    const reportId = pendingFocusReportId.current;
    if (reportId === null) return;

    if (resultsPhase === "idle" || resultsPhase === "loading") {
      pendingFocusSawReload.current = true;
      return;
    }
    if (query.report !== null) return;

    const trigger = reportTriggerNodes.current.get(reportId);
    if (trigger !== undefined) {
      trigger.focus();
      if (pendingFocusSawReload.current || trigger !== pendingFocusOriginalNode.current) {
        clearPendingFocus();
      }
      return;
    }

    const fallback = reportListRef.current ?? resultsStatusRef.current;
    fallback?.focus();
    clearPendingFocus();
  }, [clearPendingFocus, query.report, reports, resultsPhase]);

  useEffect(() => {
    const reportId = query.report;
    if (reportId === null || localSelection.current === reportId) return;

    if (reportFromResults !== null) {
      if (attemptedDeepLink.current !== reportId) {
        attemptedDeepLink.current = reportId;
        queueMicrotask(() => {
          setDeepLinkReport(reportFromResults);
          setDeepLinkMessage(null);
        });
      }
      return;
    }

    if (
      (resultsPhase !== "ready" && resultsPhase !== "empty") ||
      attemptedDeepLink.current === reportId
    ) {
      return;
    }

    attemptedDeepLink.current = reportId;
    const controller = new AbortController();
    let active = true;
    let settled = false;
    getPublicReport(reportId, controller.signal)
      .then((report) => {
        if (!active) return;
        settled = true;
        const reportState =
          report.status === "RESUELTO" || report.status === "CERRADO" ? "resolved" : "active";
        const queryChanges: Parameters<typeof updateMapQuery>[1] = {};
        if (query.stateFilter !== "all" && query.stateFilter !== reportState) {
          queryChanges.estado = reportState;
        }
        if (query.category !== null && query.category !== report.category.slug) {
          queryChanges.categoria = report.category.slug;
        }

        setDeepLinkReport(report);
        setDeepLinkFocus({
          id: report.id,
          latitude: report.latitude,
          longitude: report.longitude,
        });
        setDeepLinkMessage(null);
        if (Object.keys(queryChanges).length > 0) replaceQuery(queryChanges);
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return;
        settled = true;
        if (error instanceof PublicApiError && error.status === 404) {
          setDeepLinkMessage("El reporte compartido no existe o no está disponible públicamente.");
          replaceQuery({ reporte: null });
        } else if (error instanceof PublicApiError && error.status === 429) {
          setDeepLinkMessage(
            "No se pudo abrir el reporte compartido porque se alcanzó el límite de consultas.",
          );
        } else {
          setDeepLinkMessage(
            "No se pudo abrir el reporte compartido. Intentá nuevamente más tarde.",
          );
        }
      });

    return () => {
      active = false;
      controller.abort();
      if (!settled && attemptedDeepLink.current === reportId) {
        attemptedDeepLink.current = null;
      }
    };
  }, [
    query.category,
    query.report,
    query.stateFilter,
    replaceQuery,
    reportFromResults,
    resultsPhase,
  ]);

  const handleViewport = useCallback((bbox: MapBbox, zoom: number) => {
    setViewport((current) => {
      if (
        current !== null &&
        current.zoom === zoom &&
        current.bbox.west === bbox.west &&
        current.bbox.south === bbox.south &&
        current.bbox.east === bbox.east &&
        current.bbox.north === bbox.north
      ) {
        return current;
      }
      return { bbox, zoom };
    });
  }, []);

  const handleSelect = useCallback(
    (report: PublicReport, trigger?: HTMLElement) => {
      clearPendingFocus();
      localSelection.current = report.id;
      focusOrigin.current = { reportId: report.id, node: trigger ?? null };
      setDeepLinkReport(report);
      setDeepLinkFocus(null);
      setDeepLinkMessage(null);
      replaceQuery({ reporte: report.id });
    },
    [clearPendingFocus, replaceQuery],
  );

  const handleClose = useCallback(() => {
    setMobileView("list");
    const origin = focusOrigin.current;
    focusOrigin.current = null;
    pendingFocusReportId.current = origin?.reportId ?? null;
    pendingFocusOriginalNode.current = origin?.node ?? null;
    pendingFocusSawReload.current = false;
    localSelection.current = null;
    attemptedDeepLink.current = null;
    setDeepLinkReport(null);
    setDeepLinkFocus(null);
    replaceQuery({ reporte: null });
  }, [replaceQuery]);

  const detailHref = useCallback(
    (report: PublicReport) => {
      const params = new URLSearchParams();
      if (query.stateFilter !== "active") params.set("estado", query.stateFilter);
      if (effectiveCategory !== null) params.set("categoria", effectiveCategory);
      params.set("reporte", report.id);
      return `/reporte/${report.id}?${params.toString()}`;
    },
    [effectiveCategory, query.stateFilter],
  );

  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="relative flex min-w-0 flex-1 flex-col bg-canvas lg:h-[calc(100dvh-var(--public-header-height))] lg:min-h-[38rem] lg:flex-none lg:flex-row lg:overflow-hidden"
    >
      <div className="flex gap-2 p-3 lg:hidden" role="group" aria-label="Vista del mapa">
        {(
          [
            ["list", "Lista"],
            ["map", "Mapa"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={mobileView === value}
            onClick={() => setMobileView(value)}
            className="min-h-11 flex-1 rounded-lg border border-border bg-surface px-4 font-semibold text-primary aria-pressed:bg-primary aria-pressed:text-on-primary focus-visible:outline-2 focus-visible:outline-focus"
          >
            {label}
          </button>
        ))}
      </div>
      <div
        className={`${mobileView === "list" ? "flex" : "hidden"} min-w-0 flex-col gap-4 bg-surface p-4 lg:flex lg:min-h-0 lg:w-[380px] lg:shrink-0 lg:overflow-y-auto lg:[&>*]:shrink-0`}
      >
        <header>
          <p className="text-sm font-semibold text-primary">Participación abierta</p>
          <h1 className="mt-1 text-2xl font-bold text-primary">Mapa público de reportes</h1>
          <p className="mt-2 text-sm text-muted">
            Explorá reportes urbanos con ubicación aproximada. Acercá el mapa y usá los filtros para
            consultar la zona visible.
          </p>
          <p className="mt-3 font-semibold text-primary">
            {resultsPhase === "ready" || resultsPhase === "empty"
              ? `${reports.length} reportes del área visible`
              : "Consultá el área visible"}
          </p>
          <Link
            href="/transparencia"
            className="inline-flex min-h-11 items-center text-sm font-semibold text-primary underline"
          >
            Ver estadísticas
          </Link>
        </header>
        <MapFilters
          stateFilter={query.stateFilter}
          category={categoryIsKnown ? query.category : null}
          categories={categories.items}
          categoriesLoading={categories.phase === "loading"}
          categoriesError={categories.phase === "error"}
          onStateChange={(value: PublicReportStateFilter) =>
            replaceQuery({ estado: value, reporte: null })
          }
          onCategoryChange={(value) => replaceQuery({ categoria: value, reporte: null })}
        />
        <div
          ref={resultsStatusRef}
          tabIndex={-1}
          aria-label="Estado de resultados del mapa"
          className="min-h-6 text-sm text-muted"
          aria-live="polite"
        >
          <MapResultsStatus phase={resultsPhase} />
          {deepLinkMessage !== null && (
            <p role="alert" className="text-warning-ink">
              {deepLinkMessage}
            </p>
          )}
        </div>
        <ReportList
          listRef={reportListRef}
          reports={reports}
          selectedId={selectedReport?.id ?? null}
          detailHref={detailHref}
          onSelect={handleSelect}
          onTriggerRef={handleReportTriggerRef}
        />
      </div>
      <div
        className={`${mobileView === "map" ? "relative" : "invisible absolute inset-x-0 pointer-events-none"} min-w-0 flex-1 lg:pointer-events-auto lg:visible lg:relative`}
      >
        <MapView
          detailHref={detailHref}
          reports={reports}
          viewport={viewport}
          selectedId={selectedReport?.id ?? null}
          focusTarget={deepLinkFocus?.id === query.report ? deepLinkFocus : null}
          onViewport={handleViewport}
          onSelect={handleSelect}
        />
      </div>
      {selectedReport !== null && (
        <ReportSidePanel
          report={selectedReport}
          detailHref={detailHref(selectedReport)}
          onClose={handleClose}
        />
      )}
    </main>
  );
}
