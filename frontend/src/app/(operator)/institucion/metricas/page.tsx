import type { Metadata } from "next";
import { OperatorMetricsPage } from "@/features/metrics/OperatorMetricsPage";
export const metadata: Metadata = { title: "Métricas de tu institución | Ciudadanía Activa" };
export default function Page() {
  return <OperatorMetricsPage />;
}
