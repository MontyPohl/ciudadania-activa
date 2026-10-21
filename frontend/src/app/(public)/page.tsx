"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { getHealth } from "@/lib/api/health";
import { StatusPill } from "@/components/ui/StatusPill";
import type { HealthStatus } from "@/types/api";

type HealthState =
  | { phase: "loading" }
  | { phase: "ready"; health: HealthStatus }
  | { phase: "error"; message: string };

export default function Home() {
  const [state, setState] = useState<HealthState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;

    getHealth()
      .then((health) => {
        if (!cancelled) setState({ phase: "ready", health });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError ? error.message : "No se pudo contactar al backend.";
        setState({ phase: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center gap-8 px-6 py-16 text-center"
    >
      <div>
        <h1 className="text-4xl font-bold text-brand-purple">Ciudadanía Activa</h1>
        <p className="mt-2 text-base text-black/70">
          Plataforma de reportes urbanos ciudadanos e incentivos por participación.
        </p>
      </div>

      <div className="flex flex-col items-center gap-3 rounded-2xl border border-black/10 px-8 py-6">
        <h2 className="font-semibold text-brand-black">Estado del backend</h2>

        {state.phase === "loading" && <StatusPill tone="pending" label="Consultando backend…" />}

        {state.phase === "ready" && (
          <div className="flex flex-wrap items-center justify-center gap-2">
            <StatusPill tone="ok" label="Backend conectado" />
            <StatusPill
              tone={state.health.db ? "ok" : "error"}
              label={state.health.db ? "Base de datos OK" : "Base de datos con errores"}
            />
            <StatusPill
              tone={state.health.redis ? "ok" : "error"}
              label={state.health.redis ? "Redis OK" : "Redis con errores"}
            />
          </div>
        )}

        {state.phase === "error" && <StatusPill tone="error" label={state.message} />}
      </div>
    </main>
  );
}
