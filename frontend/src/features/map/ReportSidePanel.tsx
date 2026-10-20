"use client";

import { PublicPhoto } from "./PublicPhoto";
import Link from "next/link";
import { useEffect, useRef } from "react";

import { ReportFields } from "@/features/map/ReportFields";
import type { PublicReport } from "@/types/api";

interface ReportSidePanelProps {
  report: PublicReport;
  detailHref: string;
  onClose: () => void;
}

export function ReportSidePanel({ report, detailHref, onClose }: ReportSidePanelProps) {
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, report.id]);

  return (
    <aside
      ref={panelRef}
      tabIndex={-1}
      aria-labelledby="report-panel-title"
      className="fixed inset-x-3 bottom-3 z-[1000] max-h-[45dvh] overflow-y-auto rounded-2xl border border-black/10 bg-white p-5 shadow-2xl outline-none focus-visible:ring-2 focus-visible:ring-brand-purple lg:absolute lg:inset-x-auto lg:top-4 lg:right-4 lg:bottom-auto lg:max-h-[calc(100%-6rem)] lg:w-80"
    >
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-primary uppercase">Reporte</p>
          <h2 id="report-panel-title" className="text-xl font-bold">
            {report.category.name}
          </h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-black/15 px-3 py-1 text-sm font-medium"
          aria-label="Cerrar ficha del reporte"
        >
          Cerrar
        </button>
      </div>

      <PublicPhoto report={report} thumbnail />
      <ReportFields report={report} />

      <Link
        href={detailHref}
        className="mt-6 inline-flex rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white"
      >
        Ver detalle
      </Link>
    </aside>
  );
}
