import { formatReportDate, REPORT_STATUS_LABELS } from "@/features/reports/presentation";
import type { ReportHistoryEntry } from "@/types/api";

export function ReportTimeline({ entries }: { entries: ReportHistoryEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-brand-black/65">Todavía no hay movimientos registrados.</p>;
  }

  return (
    <ol
      aria-label="Historial de estados"
      className="space-y-4 border-l-2 border-brand-purple/20 pl-5"
    >
      {entries.map((entry, index) => (
        <li key={`${entry.created_at}-${index}`} className="relative">
          <span
            aria-hidden="true"
            className="absolute -left-[1.72rem] top-1 size-3 rounded-full bg-brand-purple ring-4 ring-white"
          />
          <p className="font-semibold text-brand-purple">
            {entry.previous_status === ""
              ? "Reporte creado"
              : `${REPORT_STATUS_LABELS[entry.previous_status]} → ${REPORT_STATUS_LABELS[entry.new_status]}`}
          </p>
          {entry.reason.trim() !== "" && (
            <p className="mt-1 whitespace-pre-wrap text-sm text-brand-black/75">{entry.reason}</p>
          )}
          <time className="mt-1 block text-xs text-brand-black/60" dateTime={entry.created_at}>
            {formatReportDate(entry.created_at)}
          </time>
        </li>
      ))}
    </ol>
  );
}
