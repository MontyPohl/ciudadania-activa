"use client";
import { Pagination } from "@/components/ui/Pagination";
import { Button } from "@/components/ui/Button";
import { useEffect, useState, type ReactNode } from "react";
import type { PaginatedResponse } from "@/types/api";
import { errorMessage } from "./presentation";
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
        <Button
          variant="secondary"
          onClick={() => {
            setView({});
            setRetry((r) => r + 1);
          }}
        >
          Reintentar
        </Button>
      </div>
    );
  if (!view.data) return <p role="status">Cargando…</p>;
  return (
    <div className="space-y-4">
      {view.data.count === 0 ? <p>{empty}</p> : render(view.data.results)}
      {(view.data.previous || view.data.next) && (
        <Pagination
          page={page}
          hasPrevious={Boolean(view.data.previous)}
          hasNext={Boolean(view.data.next)}
          onPageChange={(nextPage) => {
            setView({});
            setPage(nextPage);
          }}
        />
      )}
    </div>
  );
}
