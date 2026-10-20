import type { Metadata } from "next";
import { AdminMetricsPage } from "@/features/metrics/AdminMetricsPage";
export const metadata: Metadata = { title: "Métricas de atención | Ciudadanía Activa" };
export default function Page() {
  return <AdminMetricsPage />;
}
