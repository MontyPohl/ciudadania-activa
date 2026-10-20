import type { Metadata } from "next";

import { OperatorReportQueue } from "@/features/reports/operator/OperatorReportQueue";

export const metadata: Metadata = { title: "Bandeja institucional | Ciudadanía Activa" };

export default function OperatorQueuePage() {
  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-6 py-10"
    >
      <header>
        <h1 className="text-2xl font-bold text-brand-purple">Bandeja institucional</h1>
        <p className="text-sm text-brand-black/70">
          Revisá los reportes dentro de tu contexto institucional activo.
        </p>
      </header>
      <OperatorReportQueue />
    </main>
  );
}
