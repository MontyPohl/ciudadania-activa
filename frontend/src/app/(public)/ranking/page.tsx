import type { Metadata } from "next";

import { MonthlyRankingPage } from "@/features/ranking/MonthlyRankingPage";

export const metadata: Metadata = {
  title: "Ranking mensual | Ciudadanía Activa",
  description: "Los 10 ciudadanos con más puntos de impacto acreditados en el mes en curso.",
};

export default function RankingPage() {
  return <MonthlyRankingPage />;
}
