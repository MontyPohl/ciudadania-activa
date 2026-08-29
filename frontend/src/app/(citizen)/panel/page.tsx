import type { Metadata } from "next";

import { CitizenPanelEntry } from "@/features/reports/panel/CitizenPanelEntry";

export const metadata: Metadata = {
  title: "Panel ciudadano | Ciudadanía Activa",
  description: "Consultá el resumen y el seguimiento de tus reportes ciudadanos.",
};

export default function CitizenPanelPage() {
  return <CitizenPanelEntry />;
}
