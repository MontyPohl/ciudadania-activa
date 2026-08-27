import type { Metadata } from "next";
import { Suspense } from "react";

import { PublicReportDetail } from "@/features/map/PublicReportDetail";

export const metadata: Metadata = {
  title: "Reporte público | Ciudadanía Activa",
  description: "Detalle público de un reporte urbano.",
};

export default async function PublicReportPage({ params }: PageProps<"/reporte/[id]">) {
  const { id } = await params;
  return (
    <Suspense
      fallback={
        <p className="p-6" role="status">
          Cargando el reporte…
        </p>
      }
    >
      <PublicReportDetail id={id} />
    </Suspense>
  );
}
