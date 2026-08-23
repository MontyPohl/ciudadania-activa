import type {
  ISODateTime,
  RankingVisibility,
  UserRole,
  UUID,
  VerificationStatus,
} from "@/types/domain";

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

/* -------------------------------------------------------------------------- */
/* Paginación                                                                 */
/* -------------------------------------------------------------------------- */

/** Envoltorio de `PageNumberPagination` de DRF. `next`/`previous` son URL absolutas. */
export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/* -------------------------------------------------------------------------- */
/* Verificación de identidad — lado ciudadano (Sprint 4)                      */
/* -------------------------------------------------------------------------- */

/**
 * Respuesta de `GET /api/v1/identity/status/`.
 *
 * Son exactamente cuatro campos. No existe `submitted_at`: el backend no guarda esa
 * columna y `updated_at` cambia también al configurar la privacidad del ranking, así
 * que no significaría lo que aparenta.
 *
 * `reviewed_at` es el instante de la **revisión** administrativa, se haya aprobado o
 * rechazado.
 */
export interface IdentityStatus {
  has_submission: boolean;
  verification_status: VerificationStatus;
  reviewed_at: ISODateTime | null;
  rejection_reason: string;
}

/** Respuesta de `POST /api/v1/identity/submit/`. */
export interface IdentitySubmitResult {
  verification_status: VerificationStatus;
}

/** Entrada de `PATCH /api/v1/profile/ranking-visibility/`. */
export interface RankingVisibilityPayload {
  ranking_visibility: RankingVisibility;
  /** Ausente significa «no lo cambies»: el backend conserva el alias guardado. */
  ranking_alias?: string;
}

export interface RankingVisibilityResult {
  ranking_visibility: RankingVisibility;
  ranking_alias: string;
}

/* -------------------------------------------------------------------------- */
/* Verificación de identidad — panel administrativo (Sprint 4)                */
/* -------------------------------------------------------------------------- */

/** Identificación del ciudadano bajo revisión. Solo visible para administradores. */
export interface IdentityReviewUser {
  id: UUID;
  email: string;
  full_name: string;
}

/**
 * Fila de la cola de revisión.
 *
 * `created_at` es el del perfil y sirve para ordenar: **no** es la fecha del último
 * reenvío, porque un reenvío reutiliza el perfil existente.
 */
export interface IdentityReviewItem {
  id: UUID;
  user: IdentityReviewUser;
  created_at: ISODateTime;
  verification_status: VerificationStatus;
}

/**
 * Detalle de una verificación.
 *
 * `selfie_signed_url` es una URL firmada de vida corta (300 s). Vive únicamente en el
 * estado del componente mientras la página está abierta: no se guarda, no se cachea y
 * no se muestra como texto.
 */
export interface IdentityReviewDetail extends IdentityReviewItem {
  reviewed_at: ISODateTime | null;
  rejection_reason: string;
  verified_by: UUID | null;
  selfie_signed_url: string | null;
}

/** Las dos decisiones posibles: el estado pendiente no es una decisión. */
export type IdentityDecision = Exclude<VerificationStatus, "PENDIENTE">;

/** Respuesta de `PATCH /api/v1/admin/identity/{id}/review/`. */
export interface IdentityReviewResult {
  id: UUID;
  verification_status: VerificationStatus;
  reviewed_at: ISODateTime | null;
  verified_by: UUID | null;
}
