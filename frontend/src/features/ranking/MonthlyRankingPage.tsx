"use client";

import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
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

export function MonthlyRankingPage({ embedded = false }: { embedded?: boolean }) {
  const Container = embedded ? "section" : "main";
  const Heading = embedded ? "h2" : "h1";
  const session = useSession();
  const [reload, setReload] = useState(0);
  const viewer = session.status === "authenticated" ? session.user.id : session.status;

  return (
    <Container
      id={embedded ? undefined : "contenido-principal"}
      tabIndex={-1}
      className="mx-auto w-full max-w-2xl space-y-6 rounded-2xl bg-surface px-4 py-6 shadow-sm sm:px-6"
    >
      {session.status === "loading" ? (
        <>
          <Heading className="text-2xl font-bold text-primary">Ranking del mes</Heading>
          <p role="status" className="text-muted">
            Cargando el ranking…
          </p>
        </>
      ) : (
        <RankingContent
          key={`${viewer}:${reload}`}
          embedded={embedded}
          onRetry={() => setReload((value) => value + 1)}
        />
      )}
      <Link
        href="/perfil"
        className="inline-block py-2 font-semibold text-primary underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4"
      >
        Configurá cómo aparecés
      </Link>
    </Container>
  );
}

function RankingContent({ onRetry, embedded }: { onRetry: () => void; embedded: boolean }) {
  const Heading = embedded ? "h2" : "h1";
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
        <Heading className="text-2xl font-bold text-primary">
          {state.phase === "ready"
            ? `Ranking de ${periodLabel(state.ranking.period)}`
            : "Ranking del mes"}
        </Heading>
        <p className="text-muted">Los 10 ciudadanos con más puntos acreditados este mes.</p>
      </div>
      {state.phase === "loading" && (
        <p role="status" className="text-muted">
          Cargando el ranking…
        </p>
      )}
      {state.phase === "error" && (
        <div className="space-y-3">
          <p role="alert" className="text-ink">
            {state.message}
          </p>
          <Button
            variant="secondary"
            type="button"
            onClick={onRetry}
            className="min-h-11 rounded-lg border border-primary px-4 py-2 font-semibold text-primary hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-offset-4"
          >
            Reintentar
          </Button>
        </div>
      )}
      {state.phase === "ready" &&
        (state.ranking.entries.length === 0 ? (
          <p role="status" className="rounded-xl bg-ink/5 p-6 text-muted">
            Todavía no hay puntos acreditados este mes
          </p>
        ) : (
          <ol aria-label="Ranking mensual" className="space-y-2">
            {state.ranking.entries.map((entry) => (
              <Card
                as="li"
                key={entry.position}
                value={entry.position}
                className={`flex items-center gap-3 rounded-xl border p-4 ${entry.is_me ? "border-primary bg-primary/10" : "border-border bg-white"}`}
              >
                <span className="w-6 shrink-0 text-center font-semibold tabular-nums text-muted">
                  {entry.position}
                </span>
                <div className="min-w-0 flex-1">
                  <span
                    className={`break-words ${entry.is_private ? "italic text-muted" : "font-semibold text-ink"}`}
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
                    <span className="ml-2 inline-block rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-white">
                      Vos
                    </span>
                  )}
                </div>
                <span className="shrink-0 text-right text-sm tabular-nums text-ink">
                  <strong className="font-semibold">{entry.points.toLocaleString("es-PY")}</strong>
                  <span className="block text-xs">puntos</span>
                </span>
              </Card>
            ))}
          </ol>
        ))}
    </>
  );
}
