"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useSession } from "@/features/auth/useSession";
import { ApiError } from "@/lib/api/client";
import { getMonthlyRanking } from "@/lib/api/ranking";
import type { MonthlyRanking } from "@/types/api";

type RankingState =
  | { phase: "loading" }
  | { phase: "ready"; ranking: MonthlyRanking }
  | { phase: "error"; message: string };

function periodLabel(period: string): string {
  const [year, month] = period.split("-").map(Number);
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, 1);
  return new Intl.DateTimeFormat("es-PY", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  })
    .format(date)
    .replace(" de ", " ");
}

export function MonthlyRankingPage() {
  const session = useSession();
  const [reload, setReload] = useState(0);
  const viewer = session.status === "authenticated" ? session.user.id : session.status;

  return (
    <main className="mx-auto w-full max-w-2xl space-y-6 px-6 py-10">
      {session.status === "loading" ? (
        <>
          <h1 className="text-2xl font-bold text-brand-purple">Ranking del mes</h1>
          <p role="status" className="text-brand-black/70">
            Cargando el ranking…
          </p>
        </>
      ) : (
        <RankingContent
          key={`${viewer}:${reload}`}
          onRetry={() => setReload((value) => value + 1)}
        />
      )}
      <Link
        href="/perfil"
        className="inline-block py-2 font-semibold text-brand-purple underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4"
      >
        Configurá cómo aparecés
      </Link>
    </main>
  );
}

function RankingContent({ onRetry }: { onRetry: () => void }) {
  const [state, setState] = useState<RankingState>({ phase: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    getMonthlyRanking(controller.signal)
      .then((ranking) => {
        if (!controller.signal.aborted) setState({ phase: "ready", ranking });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          phase: "error",
          message:
            error instanceof ApiError && error.status === 429
              ? "Se alcanzó el límite de consultas. Intentá de nuevo en un momento."
              : "No se pudo cargar el ranking. Intentá de nuevo.",
        });
      });
    return () => controller.abort();
  }, []);

  return (
    <>
      <div className="space-y-2">
        <h1 className="text-2xl font-bold text-brand-purple">
          {state.phase === "ready"
            ? `Ranking de ${periodLabel(state.ranking.period)}`
            : "Ranking del mes"}
        </h1>
        <p className="text-brand-black/70">
          Los 10 ciudadanos con más puntos acreditados este mes.
        </p>
      </div>
      {state.phase === "loading" && (
        <p role="status" className="text-brand-black/70">
          Cargando el ranking…
        </p>
      )}
      {state.phase === "error" && (
        <div className="space-y-3">
          <p role="alert" className="text-brand-black/80">
            {state.message}
          </p>
          <button
            type="button"
            onClick={onRetry}
            className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple hover:bg-brand-purple/5 focus-visible:outline-2 focus-visible:outline-offset-4"
          >
            Reintentar
          </button>
        </div>
      )}
      {state.phase === "ready" &&
        (state.ranking.entries.length === 0 ? (
          <p role="status" className="rounded-xl bg-brand-black/5 p-6 text-brand-black/70">
            Todavía no hay puntos acreditados este mes
          </p>
        ) : (
          <ol aria-label="Ranking mensual" className="space-y-2">
            {state.ranking.entries.map((entry) => (
              <li
                key={entry.position}
                value={entry.position}
                className={`flex items-center gap-3 rounded-xl border p-4 ${entry.is_me ? "border-brand-purple bg-brand-purple/10" : "border-brand-black/10 bg-white"}`}
              >
                <span className="w-6 shrink-0 text-center font-semibold tabular-nums text-brand-black/60">
                  {entry.position}
                </span>
                <div className="min-w-0 flex-1">
                  <span
                    className={`break-words ${entry.is_private ? "italic text-brand-black/60" : "font-semibold text-brand-black"}`}
                  >
                    {entry.is_private && (
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        className="mr-1 inline-block h-4 w-4 align-text-bottom"
                      >
                        <rect x="5" y="10" width="14" height="11" rx="2" />
                        <path d="M8 10V7a4 4 0 0 1 8 0v3" />
                      </svg>
                    )}
                    {entry.display_name}
                  </span>
                  {entry.is_me && (
                    <span className="ml-2 inline-block rounded-full bg-brand-purple px-2 py-0.5 text-xs font-semibold text-white">
                      Vos
                    </span>
                  )}
                </div>
                <span className="shrink-0 text-right text-sm tabular-nums text-brand-black/80">
                  <strong className="font-semibold">{entry.points.toLocaleString("es-PY")}</strong>
                  <span className="block text-xs">puntos</span>
                </span>
              </li>
            ))}
          </ol>
        ))}
    </>
  );
}
