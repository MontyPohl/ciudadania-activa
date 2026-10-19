"use client";
import { useEffect, useState, type ReactNode } from "react";
import type { PaginatedResponse } from "@/types/api";
import { errorMessage, secondary } from "./presentation";
export function PaginatedList<T>({
  load,
  render,
  empty,
}: {
  load: (page: number, signal: AbortSignal) => Promise<PaginatedResponse<T>>;
  render: (items: T[]) => ReactNode;
  empty: string;
}) {
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [view, setView] = useState<{ data?: PaginatedResponse<T>; error?: string }>({});
  useEffect(() => {
    const controller = new AbortController();
    load(page, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setView({ data });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setView({ error: errorMessage(error) });
      });
    return () => controller.abort();
  }, [load, page, retry]);
  if (view.error)
    return (
      <div className="space-y-3">
        <p role="alert">{view.error}</p>
        <button
          className={secondary}
          onClick={() => {
            setView({});
            setRetry((r) => r + 1);
          }}
        >
          Reintentar
        </button>
      </div>
    );
  if (!view.data) return <p role="status">Cargando…</p>;
  return (
    <div className="space-y-4">
      {view.data.count === 0 ? <p>{empty}</p> : render(view.data.results)}
      {(view.data.previous || view.data.next) && (
        <nav aria-label="Paginación" className="flex flex-wrap items-center gap-3">
          <button
            className={secondary}
            disabled={!view.data.previous}
            onClick={() => {
              setView({});
              setPage((p) => p - 1);
            }}
          >
            Anterior
          </button>
          <span>Página {page}</span>
          <button
            className={secondary}
            disabled={!view.data.next}
            onClick={() => {
              setView({});
              setPage((p) => p + 1);
            }}
          >
            Siguiente
          </button>
        </nav>
      )}
    </div>
  );
}
