/** `refreshing` es una recarga del viewport que mantiene visibles los resultados anteriores. */
export type MapResultsPhase =
  "idle" | "loading" | "refreshing" | "ready" | "empty" | "too-wide" | "error" | "rate-limited";

export function MapResultsStatus({ phase }: { phase: MapResultsPhase }) {
  if (phase === "idle" || phase === "refreshing") return null;
  if (phase === "loading") return <p role="status">Consultando reportes visibles…</p>;
  if (phase === "empty")
    return <p role="status">No hay reportes para estos filtros y esta zona.</p>;
  if (phase === "too-wide") {
    return <p role="status">Acercá el mapa para consultar reportes en esta zona.</p>;
  }
  if (phase === "rate-limited") {
    return (
      <p role="alert" className="text-danger-ink">
        Se alcanzó el límite de consultas. Esperá un momento antes de mover el mapa otra vez.
      </p>
    );
  }
  if (phase === "error") {
    return (
      <p role="alert" className="text-danger-ink">
        No se pudieron cargar los reportes. Podés seguir navegando el mapa e intentar de nuevo.
      </p>
    );
  }
  return null;
}

const MAP_NOTICES: Partial<Record<MapResultsPhase, { text: string; danger?: boolean }>> = {
  loading: { text: "Consultando reportes…" },
  refreshing: { text: "Actualizando reportes…" },
  empty: { text: "No hay reportes en esta zona." },
  "too-wide": { text: "Acercá el mapa para ver reportes." },
  error: { text: "No se pudieron cargar los reportes.", danger: true },
  "rate-limited": { text: "Límite de consultas alcanzado. Esperá un momento.", danger: true },
};

/**
 * Aviso sobre el mapa, visible también cuando la lista está oculta (vista «Mapa» en móvil).
 * Sólo anuncia por lector de pantalla cuando el estado de la lista no está disponible.
 */
export function MapNotice({ phase, announce }: { phase: MapResultsPhase; announce: boolean }) {
  const notice = MAP_NOTICES[phase];
  const pending = phase === "loading" || phase === "refreshing";
  return (
    <div
      aria-live={announce ? "polite" : undefined}
      className="pointer-events-none absolute top-3 left-1/2 z-[500] w-max max-w-[calc(100%-7rem)] -translate-x-1/2"
    >
      {notice !== undefined && (
        <p
          key={phase}
          aria-hidden={pending || undefined}
          className={`rounded-full bg-surface/95 px-4 py-2 text-center text-sm font-semibold shadow-lg ${notice.danger ? "text-danger-ink" : "text-primary"}${pending ? " ca-map-notice--delayed" : ""}`}
        >
          {notice.text}
        </p>
      )}
    </div>
  );
}
