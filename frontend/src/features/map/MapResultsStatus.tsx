export type MapResultsPhase =
  "idle" | "loading" | "ready" | "empty" | "too-wide" | "error" | "rate-limited";

export function MapResultsStatus({ phase }: { phase: MapResultsPhase }) {
  if (phase === "idle") return null;
  if (phase === "loading") return <p role="status">Consultando reportes visibles…</p>;
  if (phase === "empty")
    return <p role="status">No hay reportes para estos filtros y esta zona.</p>;
  if (phase === "too-wide") {
    return <p role="status">Acercá el mapa para consultar reportes en esta zona.</p>;
  }
  if (phase === "rate-limited") {
    return (
      <p role="alert" className="text-brand-orange">
        Se alcanzó el límite de consultas. Esperá un momento antes de mover el mapa otra vez.
      </p>
    );
  }
  if (phase === "error") {
    return (
      <p role="alert" className="text-brand-orange">
        No se pudieron cargar los reportes. Podés seguir navegando el mapa e intentar de nuevo.
      </p>
    );
  }
  return null;
}
