import { formatReportDate, REPORT_STATUS_LABELS } from "@/features/reports/presentation";
import type { ReportHistoryEntry } from "@/types/api";

export function ReportTimeline({ entries }: { entries: ReportHistoryEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-muted">Todavía no hay movimientos registrados.</p>;
  }

  return (
    <ol aria-label="Historial de estados" className="space-y-4 border-l-2 border-primary/20 pl-5">
      {entries.map((entry, index) => (
        <li key={`${entry.created_at}-${index}`} className="relative">
          <span
            aria-hidden="true"
            className="absolute -left-[1.72rem] top-1 size-3 rounded-full bg-primary ring-4 ring-surface"
          />
          <p className="font-semibold text-primary">
            {entry.previous_status === ""
              ? "Reporte creado"
              : `${REPORT_STATUS_LABELS[entry.previous_status]} → ${REPORT_STATUS_LABELS[entry.new_status]}`}
          </p>
          {entry.cycle > 0 && (
            <p className="mt-1 text-xs font-semibold uppercase tracking-wide text-muted">
              Ciclo {entry.cycle}
            </p>
          )}
          {entry.reason.trim() !== "" && (
            <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{entry.reason}</p>
          )}
          <time className="mt-1 block text-xs text-muted" dateTime={entry.created_at}>
            {formatReportDate(entry.created_at)}
          </time>
        </li>
      ))}
    </ol>
  );
}
