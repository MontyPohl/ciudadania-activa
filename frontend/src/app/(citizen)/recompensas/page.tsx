import type { Metadata } from "next";
import { RewardsEntry } from "@/features/rewards/RewardsEntry";
export const metadata: Metadata = { title: "Recompensas | Ciudadanía Activa" };
export default function Page() {
  return <RewardsEntry />;
}
