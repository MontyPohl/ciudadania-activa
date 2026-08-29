import type { SessionUser } from "@/types/api";

export type CitizenAccess = "anonymous" | "wrong-role" | "citizen-unverified" | "citizen-verified";

/** Decisión de presentación; la autorización definitiva permanece en Django. */
export function citizenAccess(
  user: Pick<SessionUser, "role" | "verification_status"> | null,
): CitizenAccess {
  if (user === null) return "anonymous";
  if (user.role !== "citizen") return "wrong-role";
  return user.verification_status === "APROBADO" ? "citizen-verified" : "citizen-unverified";
}
