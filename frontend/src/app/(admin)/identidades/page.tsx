"use client";

/** Bandeja administrativa de verificaciones de identidad (RF-30). */

import { ReviewQueue } from "@/features/identity/ReviewQueue";

export default function IdentidadesPage() {
  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full min-w-0 max-w-4xl flex-col gap-6 px-6 py-10"
    >
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-primary">Verificaciones de identidad</h1>
        <p className="text-sm text-muted">
          Revisá manualmente cada envío y aprobalo o rechazalo con un motivo.
        </p>
      </header>

      <ReviewQueue />
    </main>
  );
}
