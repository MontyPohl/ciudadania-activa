"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { INITIAL_BBOX } from "@/features/map/mapConfig";
import { serializeBbox } from "@/features/map/bbox";
import { getPublicReports } from "@/lib/api/publicReports";
import type { PublicReport } from "@/types/api";
import { KpiTile } from "./charts/KpiTile";

export function zoneSummary(reports: PublicReport[], now: number) {
  const { west, east, south, north } = INITIAL_BBOX;
  const radians = Math.PI / 180;
  const area =
    6371 ** 2 *
    Math.abs(Math.sin(north * radians) - Math.sin(south * radians)) *
    (east - west) *
    radians;
  return {
    oldOpen: reports.filter(
      (r) =>
        !["RESUELTO", "CERRADO"].includes(r.status) &&
        now - Date.parse(r.created_at) > 7 * 86400000,
    ).length,
    density: reports.length / area,
  };
}
export function ZoneMetrics() {
  const [summary, setSummary] = useState<ReturnType<typeof zoneSummary> | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getPublicReports(
      { bbox: serializeBbox(INITIAL_BBOX), stateFilter: "all", category: null },
      controller.signal,
    )
      .then((page) => {
        if (!controller.signal.aborted) setSummary(zoneSummary(page.results, Date.now()));
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [retry]);
  return (
    <Card as="section" className="space-y-4" aria-label="Indicadores de la zona del mapa">
      <h2 className="text-xl font-semibold">Zona inicial del mapa</h2>
      <p className="text-sm text-muted">
        Derivados del listado público de la zona inicial del mapa, con todos los estados y
        categorías. No son totales de la ciudad. La densidad usa el área del rectángulo consultado.
      </p>
      {failed ? (
        <div>
          <p role="alert">No se pudo consultar el listado de la zona.</p>
          <Button
            variant="secondary"
            onClick={() => {
              setFailed(false);
              setRetry((n) => n + 1);
            }}
          >
            Reintentar zona
          </Button>
        </div>
      ) : summary === null ? (
        <p role="status">Consultando la zona…</p>
      ) : (
        <dl className="grid gap-4 sm:grid-cols-2">
          <KpiTile label="Reportes abiertos hace más de 7 días" value={summary.oldOpen} />
          <KpiTile
            label="Densidad"
            value={`${summary.density.toLocaleString("es-PY", { maximumFractionDigits: 3 })} reportes/km²`}
          />
        </dl>
      )}
      <Link
        href="/mapa"
        className="inline-flex min-h-11 items-center font-semibold text-primary underline"
      >
        Explorar zona en el mapa
      </Link>
    </Card>
  );
}
