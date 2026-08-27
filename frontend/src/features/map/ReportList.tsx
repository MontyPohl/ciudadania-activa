import Link from "next/link";

import { STATUS_LABELS } from "@/features/map/presentation";
import type { PublicReport } from "@/types/api";

interface ReportListProps {
  reports: PublicReport[];
  selectedId: string | null;
  detailHref: (report: PublicReport) => string;
  onSelect: (report: PublicReport, trigger: HTMLElement) => void;
}

export function ReportList({ reports, selectedId, detailHref, onSelect }: ReportListProps) {
  if (reports.length === 0) return null;

  return (
    <section
      className="rounded-2xl border border-black/10 bg-white p-4"
      aria-labelledby="visible-reports-title"
    >
      <h2 id="visible-reports-title" className="text-lg font-bold text-brand-purple">
        Reportes visibles ({reports.length})
      </h2>
      <p className="mt-1 text-xs text-black/65">
        Para navegar y seleccionar reportes con teclado, usá esta lista.
      </p>
      <ul className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {reports.map((report) => (
          <li
            key={report.id}
            className={`rounded-xl border p-3 ${report.id === selectedId ? "border-brand-purple bg-brand-purple/5" : "border-black/10"}`}
          >
            <button
              type="button"
              data-report-list-trigger={report.id}
              onClick={(event) => onSelect(report, event.currentTarget)}
              className="w-full text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
            >
              <span className="block font-semibold">{report.category.name}</span>
              <span className="mt-1 block text-xs text-black/65">
                {STATUS_LABELS[report.status]} · {report.institution?.name ?? "Sin institución"}
              </span>
              <span className="mt-2 line-clamp-2 block text-sm">{report.description}</span>
            </button>
            <Link
              href={detailHref(report)}
              className="mt-3 inline-block text-sm font-semibold text-brand-purple underline"
            >
              Abrir detalle
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
