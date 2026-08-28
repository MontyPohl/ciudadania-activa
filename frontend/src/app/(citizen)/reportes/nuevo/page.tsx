import type { Metadata } from "next";

import { CreateReportEntry } from "@/features/reports/create/CreateReportEntry";

export const metadata: Metadata = {
  title: "Crear reporte | Ciudadanía Activa",
  description: "Informá una incidencia urbana para que pueda ser revisada.",
};

export default function NewReportPage() {
  return <CreateReportEntry />;
}
