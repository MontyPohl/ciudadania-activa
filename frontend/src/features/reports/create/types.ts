import type { LocationValue } from "@/features/location/location";
import { citizenAccess, type CitizenAccess } from "@/features/auth/citizenAccess";

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

export type ReportCreationAccess = CitizenAccess;

/** Traduce la sesión ya resuelta por SessionProvider al acceso que necesitará B2. */
export function reportCreationAccess(
  user: Parameters<typeof citizenAccess>[0],
): ReportCreationAccess {
  return citizenAccess(user);
}
