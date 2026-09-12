import type { Metadata } from "next";

import { AdminAuditConsole } from "@/features/audit/AdminAuditConsole";

export const metadata: Metadata = {
  title: "Auditoría | Ciudadanía Activa",
  description: "Consulta administrativa de eventos y reversión controlada de validaciones.",
};

export default function AuditoriaPage() {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-10 sm:px-6">
      <header>
        <p className="text-sm font-semibold text-brand-green">Control administrativo</p>
        <h1 className="text-3xl font-bold text-brand-purple">Auditoría de reportes</h1>
        <p className="mt-2 max-w-3xl text-sm">
          Consultá eventos trazables y, cuando corresponda, prepará una reversión con motivo
          explícito. La operación vuelve a validar el estado y el ciclo en el servidor.
        </p>
      </header>
      <AdminAuditConsole />
    </main>
  );
}
