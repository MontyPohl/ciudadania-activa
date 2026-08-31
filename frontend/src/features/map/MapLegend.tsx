import { STATUS_LABELS, STATUS_MARKERS } from "@/features/map/presentation";
import type { PublicReportStatus } from "@/types/api";

const STATUS_MARKER_ENTRIES = Object.entries(STATUS_MARKERS) as Array<
  [PublicReportStatus, (typeof STATUS_MARKERS)[PublicReportStatus]]
>;

export function MapLegend() {
  return (
    <section
      aria-label="Leyenda de estados"
      className="ca-map-legend rounded-xl border border-black/10 bg-white/95 p-3 text-xs text-brand-black shadow-lg"
    >
      <h2 className="mb-2 font-semibold">Estados</h2>
      <ul className="grid grid-cols-2 gap-x-3 gap-y-1">
        {STATUS_MARKER_ENTRIES.map(([status, marker]) => (
          <li key={status} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold text-white"
              style={{ backgroundColor: marker.color }}
            >
              {marker.symbol}
            </span>
            <span>{STATUS_LABELS[status]}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
