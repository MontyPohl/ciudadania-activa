import type { Metadata } from "next";
import { PublicTransparencyPage } from "@/features/metrics/PublicTransparencyPage";
export const metadata: Metadata = { title: "Transparencia | Ciudadanía Activa" };
export default function Page() {
  return <PublicTransparencyPage />;
}
