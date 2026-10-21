import { STATUS_MARKERS } from "./presentation";
export function MapLegend() {
  return (
    <section
      aria-label="Leyenda de estados"
      className="ca-map-legend rounded-xl bg-surface/95 p-3 text-xs text-ink shadow-lg"
    >
      <h2 className="mb-2 font-semibold">Estados</h2>
      <ul className="space-y-1">
        {(
          [
            ["Pendiente", STATUS_MARKERS.CREADO],
            ["En proceso", STATUS_MARKERS.EN_PROCESO],
            ["Resuelto", STATUS_MARKERS.RESUELTO],
          ] as const
        ).map(([label, marker]) => (
          <li key={label} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-flex h-5 w-5 items-center justify-center rounded-full font-bold text-white"
              style={{ backgroundColor: marker.color }}
            >
              {marker.symbol}
            </span>
            {label}
          </li>
        ))}
      </ul>
    </section>
  );
}
