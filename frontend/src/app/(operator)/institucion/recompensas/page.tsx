import type { Metadata } from "next";
import { OperatorRewardsPage } from "@/features/rewards/operator/OperatorRewardsPage";
export const metadata: Metadata = { title: "Recompensas institucionales | Ciudadanía Activa" };
export default function Page() {
  return <OperatorRewardsPage />;
}
