"use client";

import { useEffect, useState } from "react";
import { Building2, CheckCheck, FileText, Percent } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useSession } from "@/features/auth/useSession";
import { INITIAL_BBOX } from "@/features/map/mapConfig";
import { STATUS_MARKERS } from "@/features/map/presentation";
import { getPublicCategories, getPublicReports } from "@/lib/api/publicReports";
import { getPublicMetrics } from "@/lib/api/publicMetrics";
import { getMonthlyRanking } from "@/lib/api/ranking";
import type { PublicReportsPage } from "@/types/api";
import { AnimatedNumber } from "./LandingMotion";
import { categoryInstitutions } from "./content";

type Remote<T> = { phase: "loading" } | { phase: "error" } | { phase: "ready"; data: T };
function useRemote<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<Remote<T>>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ phase: "ready", data });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ phase: "error" });
      });
    return () => controller.abort();
  }, [load, attempt]);
  return {
    state,
    retry: () => {
      setState({ phase: "loading" });
      setAttempt((n) => n + 1);
    },
  };
}
function Feedback({
  phase,
  label,
  retry,
}: {
  phase: "loading" | "error";
  label: string;
  retry: () => void;
}) {
  return (
    <div className="space-y-3 py-4 text-muted">
      <p role="status">
        {phase === "loading"
          ? `Cargando ${label}…`
          : `No se pudo cargar la información de ${label.replace(/^(las|los|el) /, "")}.`}
      </p>
      {phase === "error" && (
        <Button variant="secondary" onClick={retry}>
          Reintentar: {label}
        </Button>
      )}
    </div>
  );
}

export function LandingMetrics() {
  const { state, retry } = useRemote(getPublicMetrics);
  if (state.phase !== "ready")
    return <Feedback phase={state.phase} label="las métricas" retry={retry} />;
  const { totals, institutions } = state.data;
  const values = [
    { label: "Reportes recibidos", value: totals.received, icon: FileText },
    { label: "Problemas resueltos", value: totals.resolved, icon: CheckCheck },
    { label: "Instituciones participantes", value: institutions.length, icon: Building2 },
    {
      label: "Tasa de resolución",
      value: totals.resolution_rate === null ? null : totals.resolution_rate * 100,
      icon: Percent,
    },
  ];
  return (
    <dl className="grid grid-cols-1 gap-4 min-[375px]:grid-cols-2 lg:grid-cols-4">
      {values.map(({ label, value, icon: Icon }) => (
        <div key={label} className="rounded-2xl bg-surface p-6 shadow-sm">
          <Icon aria-hidden="true" className="mb-4 size-6 text-primary" />
          <dt className="text-sm text-muted">{label}</dt>
          <dd className="mt-2 text-3xl font-bold text-primary">
            {value === null ? (
              "—"
            ) : (
              <AnimatedNumber value={value} percent={label === "Tasa de resolución"} />
            )}
          </dd>
          {value === null && (
            <p className="mt-2 text-sm text-muted">
              {totals.suppressed.includes("resolution_rate")
                ? "Se muestra con 5 o más casos."
                : "Sin base para calcular la tasa."}
            </p>
          )}
        </div>
      ))}
    </dl>
  );
}

export function LandingCategories() {
  const { state, retry } = useRemote(getPublicCategories);
  if (state.phase !== "ready")
    return <Feedback phase={state.phase} label="las categorías" retry={retry} />;
  if (!state.data.length) return <p className="text-muted">No hay categorías disponibles.</p>;
  const groups = new Map<string, typeof state.data>();
  for (const category of state.data) {
    const name = categoryInstitutions[category.slug] ?? "Otras categorías";
    groups.set(name, [...(groups.get(name) ?? []), category]);
  }
  return (
    <div className="grid gap-6 md:grid-cols-3">
      {Array.from(groups, ([institution, categories]) => (
        <article key={institution} className="rounded-2xl bg-surface p-6 shadow-sm">
          <Building2 aria-hidden="true" className="mb-4 size-6 text-primary" />
          <h3 className="text-lg font-bold text-primary">{institution}</h3>
          <ul className="mt-5 space-y-3 text-ink">
            {categories.map((category) => (
              <li key={category.id} className="flex gap-3">
                <span aria-hidden="true" className="text-primary">
                  ·
                </span>
                <span>{category.name}</span>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

export function LandingRanking() {
  const session = useSession();
  const viewer = session.status === "authenticated" ? session.user.id : session.status;
  return session.status === "loading" ? (
    <p role="status" className="text-muted">
      Cargando el ranking…
    </p>
  ) : (
    <RankingData key={viewer} />
  );
}
function RankingData() {
  const { state, retry } = useRemote(getMonthlyRanking);
  if (state.phase !== "ready")
    return <Feedback phase={state.phase} label="el ranking" retry={retry} />;
  const ranking = state.data;
  const period = new Intl.DateTimeFormat("es-PY", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${ranking.period}-01T12:00:00Z`));
  return (
    <>
      <p className="mb-5 text-muted">Puntos de impacto acreditados · {period}</p>
      {!ranking.entries.length ? (
        <p className="rounded-2xl bg-surface p-6 text-muted">
          Todavía no hay puntos acreditados este mes.
        </p>
      ) : (
        <ol className="grid gap-4 md:grid-cols-3">
          {ranking.entries.slice(0, 3).map((entry) => (
            <li
              key={entry.position}
              className="flex min-w-0 items-center gap-4 rounded-2xl bg-surface p-6 shadow-sm"
            >
              <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary/10 font-bold text-primary">
                {entry.position}
              </span>
              <div className="min-w-0">
                <p className="font-semibold wrap-anywhere text-ink">{entry.display_name}</p>
                <p className="mt-1 text-sm text-muted">
                  {entry.points.toLocaleString("es-PY")} puntos
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

const { west, south, east, north } = INITIAL_BBOX;
const loadReports = (signal: AbortSignal): Promise<PublicReportsPage> =>
  getPublicReports(
    { bbox: `${west},${south},${east},${north}`, stateFilter: "active", category: null },
    signal,
  );
export function LandingPhone() {
  const { state, retry } = useRemote(loadReports);
  return (
    <figure className="mx-auto w-full max-w-72">
      <div className="rounded-[2.5rem] border-8 border-ink bg-surface p-2 shadow-xl">
        <div aria-hidden="true" className="mx-auto mb-3 h-1.5 w-16 rounded-full bg-ink" />
        <div className="rounded-t-3xl bg-primary px-4 py-5 text-center text-on-primary">
          <p className="text-xs font-semibold">Ciudadanía Activa</p>
          <p className="mt-1 font-bold">Mapa de Asunción</p>
        </div>
        <svg
          viewBox="0 0 240 300"
          role="img"
          aria-label="Mapa esquemático de Asunción con ubicaciones públicas de reportes activos"
          className="w-full bg-canvas"
        >
          <path
            d="M0 0H67L61 40 78 85 65 117 81 160 69 196 89 240 80 300H0Z"
            fill="var(--color-secondary)"
            opacity=".18"
          />
          <g stroke="var(--color-surface)" strokeWidth="7" fill="none">
            <path d="M70 40 240 122M73 93 240 175M78 156 240 237M82 218 240 294M100 0 175 300M158 0 225 264M217 0 240 105" />
          </g>
          <g fill="var(--color-muted)">
            <text x="110" y="155" fontSize="11">
              Asunción
            </text>
          </g>
          {state.phase === "ready" &&
            state.data.results
              .filter(
                (report) =>
                  report.status !== "RESUELTO" &&
                  report.status !== "CERRADO" &&
                  Number.isFinite(report.latitude) &&
                  Number.isFinite(report.longitude) &&
                  report.longitude >= west &&
                  report.longitude <= east &&
                  report.latitude >= south &&
                  report.latitude <= north,
              )
              .map((report) => {
                const x = ((report.longitude - west) / (east - west)) * 240;
                const y = ((north - report.latitude) / (north - south)) * 300;
                const marker = STATUS_MARKERS[report.status];
                return (
                  <g key={report.id} transform={`translate(${x},${y})`} aria-hidden="true">
                    <circle
                      r="10"
                      fill={marker.color}
                      stroke="var(--color-surface)"
                      strokeWidth="2"
                    />
                    <text textAnchor="middle" dy="4" fontSize="11" fill="var(--color-on-primary)">
                      {marker.symbol}
                    </text>
                  </g>
                );
              })}
        </svg>
        <div className="px-3 py-4 text-center text-xs text-muted">
          {state.phase === "ready" ? (
            state.data.results.length ? (
              "Ubicaciones públicas de reportes activos"
            ) : (
              "No hay reportes activos en esta zona."
            )
          ) : (
            <Feedback phase={state.phase} label="los reportes" retry={retry} />
          )}
        </div>
      </div>
      <figcaption className="mt-5 space-y-2 text-center text-xs text-muted">
        <p>Mapa esquemático · coordenadas públicas redondeadas</p>
        <p>
          ● Pendiente · <span className="text-warning-ink">▲ En proceso</span> ·{" "}
          <span className="text-success-ink">✓ Resuelto</span>
        </p>
      </figcaption>
    </figure>
  );
}
