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
      <p role="status" aria-live="polite" className="text-sm text-brand-black/70">
        Buscando reportes similares…
      </p>
    );
  }

  if (state.status === "error") {
    return (
      <p role="status" className="rounded-lg bg-brand-orange/10 p-3 text-sm text-brand-black/75">
        No pudimos consultar reportes similares. Esto no impide crear el reporte.
      </p>
    );
  }

  if (state.candidates.length === 0) {
    return <p className="text-sm text-brand-black/65">No encontramos reportes similares.</p>;
  }

  return (
    <section
      aria-labelledby="possible-duplicates-title"
      className="rounded-xl border border-brand-orange/35 bg-brand-orange/10 p-4"
    >
      <h3 id="possible-duplicates-title" className="font-bold text-brand-orange">
        Ya existe un reporte similar
      </h3>
      <p className="mt-1 text-sm text-brand-black/75">
        Podés revisar estos reportes o continuar con el tuyo. Este aviso no bloquea la creación.
      </p>
      <ul className="mt-3 space-y-3">
        {state.candidates.map((candidate) => (
          <li key={candidate.id} className="rounded-lg bg-white p-3 text-sm">
            <p className="font-semibold">{candidate.category.name}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-brand-black/75">
              {candidate.description}
            </p>
            <p className="mt-1 text-xs text-brand-black/60">
              Estado: {candidate.status} · Prioridad: {candidate.priority}
            </p>
            <Link
              href={`/reporte/${candidate.id}`}
              className="mt-2 inline-block font-semibold text-brand-purple underline"
            >
              Abrir reporte existente
            </Link>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={onContinue}
        className="mt-4 rounded-md border border-brand-purple px-4 py-2 text-sm font-semibold text-brand-purple"
      >
        Continuar de todos modos
      </button>
    </section>
  );
}
