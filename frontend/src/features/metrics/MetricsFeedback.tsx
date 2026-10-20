export const metricsPageClass =
  "mx-auto flex w-full max-w-6xl min-w-0 flex-col gap-6 px-4 py-8 text-ink sm:px-6 sm:py-10";
export function MetricsLoading() {
  return (
    <div role="status" aria-label="Cargando métricas" className="space-y-6">
      <span className="sr-only">Cargando métricas…</span>
      <div aria-hidden="true" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="h-36 rounded-2xl bg-primary/10 motion-safe:animate-pulse" />
        ))}
      </div>
      <div aria-hidden="true" className="h-72 rounded-2xl bg-primary/5 motion-safe:animate-pulse" />
    </div>
  );
}
export function MetricsError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-2xl border border-primary/20 bg-primary/5 p-6">
      <p role="alert">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 min-h-11 rounded-lg border border-primary px-4 py-2 font-semibold text-primary hover:bg-primary/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        Reintentar
      </button>
    </div>
  );
}
