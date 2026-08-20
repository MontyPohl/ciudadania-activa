import type { UserRole, VerificationStatus } from "@/types/domain";

export interface HealthStatus {
  status: "ok" | "degraded" | "error";
  version: string;
  db: boolean;
  redis: boolean;
}

/** Usuario autenticado, tal como lo devuelve `GET /api/v1/auth/me/`. */
export interface SessionUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  verification_status: VerificationStatus;
  is_verified_citizen: boolean;
  institutions: SessionInstitution[];
}

export interface SessionInstitution {
  id: string;
  name: string;
  slug: string;
}

/** Respuesta del BFF en `POST /api/auth/google`. El refresh nunca viaja al cliente. */
export interface SessionStart {
  access: string;
  user: SessionUser;
}

/** Respuesta del BFF en `POST /api/auth/refresh`. */
export interface SessionRefresh {
  access: string;
}
