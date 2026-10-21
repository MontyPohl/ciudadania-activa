import { Button } from "@/components/ui/Button";
import Link from "next/link";

import type { DuplicateQueryState } from "@/features/reports/create/state";

interface PossibleDuplicatesProps {
  state: DuplicateQueryState;
  onContinue: () => void;
}

export function PossibleDuplicates({ state, onContinue }: PossibleDuplicatesProps) {
  if (state.status === "idle") return null;

  if (state.status === "loading") {
    return (
      <p role="status" aria-live="polite" className="text-sm text-muted">
        Buscando reportes similares…
      </p>
    );
  }

  if (state.status === "error") {
    return (
      <p role="status" className="rounded-lg bg-warning/10 p-3 text-sm text-muted">
        No pudimos consultar reportes similares. Esto no impide crear el reporte.
      </p>
    );
  }

  if (state.candidates.length === 0) {
    return <p className="text-sm text-muted">No encontramos reportes similares.</p>;
  }

  return (
    <section
      aria-labelledby="possible-duplicates-title"
      className="rounded-xl border border-warning/35 bg-warning/10 p-4"
    >
      <h3 id="possible-duplicates-title" className="font-bold text-danger-ink">
        Ya existe un reporte similar
      </h3>
      <p className="mt-1 text-sm text-muted">
        Podés revisar estos reportes o continuar con el tuyo. Este aviso no bloquea la creación.
      </p>
      <ul className="mt-3 space-y-3">
        {state.candidates.map((candidate) => (
          <li key={candidate.id} className="rounded-lg bg-surface p-3 text-sm">
            <p className="font-semibold">{candidate.category.name}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-muted">
              {candidate.description}
            </p>
            <p className="mt-1 text-xs text-muted">
              Estado: {candidate.status} · Prioridad: {candidate.priority}
            </p>
            <Link
              href={`/reporte/${candidate.id}`}
              className="mt-2 inline-block font-semibold text-primary underline"
            >
              Abrir reporte existente
            </Link>
          </li>
        ))}
      </ul>
      <Button variant="secondary" type="button" onClick={onContinue} className="mt-4 text-sm">
        Continuar de todos modos
      </Button>
    </section>
  );
}
