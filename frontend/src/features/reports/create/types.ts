import type { LocationValue } from "@/features/location/location";
import type { SessionUser } from "@/types/api";

export interface CreateReportInput {
  categoryId: string;
  description: string;
  photo: File;
  location: LocationValue;
}

export interface CreateReportResponse {
  id: string;
  status: "CREADO";
  reward: {
    impact_points: number;
    credits: number;
    status: "PENDIENTE";
  };
}

export type ActiveReportStatus = "CREADO" | "CLASIFICADO" | "ASIGNADO" | "EN_PROCESO";

export interface PossibleDuplicateReport {
  id: string;
  category: {
    id: string;
    name: string;
    slug: string;
  };
  institution: {
    id: string;
    name: string;
    slug: string;
  } | null;
  description: string;
  status: ActiveReportStatus;
  priority: "BAJA" | "MEDIA" | "ALTA";
  created_at: string;
}

export interface PossibleDuplicatesInput {
  categoryId: string;
  location: LocationValue;
}

export type ReportCreationAccess =
  "anonymous" | "wrong-role" | "citizen-unverified" | "citizen-verified";

/** Traduce la sesión ya resuelta por SessionProvider al acceso que necesitará B2. */
export function reportCreationAccess(
  user: Pick<SessionUser, "role" | "verification_status"> | null,
): ReportCreationAccess {
  if (user === null) return "anonymous";
  if (user.role !== "citizen") return "wrong-role";
  return user.verification_status === "APROBADO" ? "citizen-verified" : "citizen-unverified";
}
