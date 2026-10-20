import { PublicPhoto } from "./PublicPhoto";
import Link from "next/link";
import { useCallback, type Ref } from "react";

import { STATUS_LABELS } from "@/features/map/presentation";
import type { PublicReport } from "@/types/api";

interface ReportListProps {
  listRef?: Ref<HTMLElement>;
  reports: PublicReport[];
  selectedId: string | null;
  detailHref: (report: PublicReport) => string;
  onSelect: (report: PublicReport, trigger: HTMLElement) => void;
  onTriggerRef?: (reportId: string, node: HTMLButtonElement | null) => void;
}

interface ReportListItemProps {
  report: PublicReport;
  selected: boolean;
  detailHref: string;
  onSelect: (report: PublicReport, trigger: HTMLElement) => void;
  onTriggerRef?: (reportId: string, node: HTMLButtonElement | null) => void;
}

function ReportListItem({
  report,
  selected,
  detailHref,
  onSelect,
  onTriggerRef,
}: ReportListItemProps) {
  const triggerRef = useCallback(
    (node: HTMLButtonElement | null) => onTriggerRef?.(report.id, node),
    [onTriggerRef, report.id],
  );

  return (
    <li
      className={`min-w-0 rounded-xl border p-3 wrap-anywhere ${selected ? "border-primary bg-primary/5" : "border-black/10"}`}
    >
      <button
        ref={triggerRef}
        type="button"
        data-report-list-trigger={report.id}
        onClick={(event) => onSelect(report, event.currentTarget)}
        className="w-full text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <PublicPhoto report={report} thumbnail />
        <span className="block font-semibold">{report.category.name}</span>
        <span className="mt-1 block text-xs text-muted">
          {STATUS_LABELS[report.status]} · {report.institution?.name ?? "Sin institución"}
        </span>
        <span className="mt-2 line-clamp-2 block text-sm">{report.description}</span>
      </button>
      <Link
        href={detailHref}
        className="mt-3 inline-block text-sm font-semibold text-primary underline"
      >
        Abrir detalle
      </Link>
    </li>
  );
}

export function ReportList({
  listRef,
  reports,
  selectedId,
  detailHref,
  onSelect,
  onTriggerRef,
}: ReportListProps) {
  if (reports.length === 0) return null;

  return (
    <section
      ref={listRef}
      tabIndex={-1}
      className="rounded-2xl border border-black/10 bg-white p-4"
      aria-labelledby="visible-reports-title"
    >
      <h2 id="visible-reports-title" className="text-lg font-bold text-primary">
        Reportes visibles ({reports.length})
      </h2>
      <p className="mt-1 text-xs text-muted">
        Para navegar y seleccionar reportes con teclado, usá esta lista.
      </p>
      <ul className="mt-3 grid gap-3">
        {reports.map((report) => (
          <ReportListItem
            key={report.id}
            report={report}
            selected={report.id === selectedId}
            detailHref={detailHref(report)}
            onSelect={onSelect}
            onTriggerRef={onTriggerRef}
          />
        ))}
      </ul>
    </section>
  );
}
