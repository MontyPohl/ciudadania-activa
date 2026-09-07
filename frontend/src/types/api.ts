import type {
  ISODateTime,
  RankingVisibility,
  RejectionReason,
  ReportPriority,
  ReportStatus,
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

/* -------------------------------------------------------------------------- */
/* Operación institucional — Sprint 9-B2                                     */
/* -------------------------------------------------------------------------- */

export interface OperatorCategory {
  id: UUID;
  name: string;
  slug: string;
}

export interface OperatorReport {
  id: UUID;
  status: ReportStatus;
  priority: ReportPriority;
  category: OperatorCategory;
  institution: SessionInstitution | null;
  effective_read_institution: SessionInstitution;
  created_at: ISODateTime;
}

export interface OperatorAiClassification {
  suggested_category: OperatorCategory | null;
  suggested_institution: SessionInstitution | null;
  suggested_priority: string;
  confidence: number | null;
  inconsistency_flag: boolean;
  justification: string;
  model_name: string;
  requires_human_review: boolean | null;
  created_at: ISODateTime;
}

export interface OperatorReportDetail extends OperatorReport {
  description: string;
  latitude: number;
  longitude: number;
  photo_signed_url: string | null;
  confirmation_count: number;
  ai_classification: OperatorAiClassification | null;
}

export interface OperatorReportActionResult {
  id: UUID;
  status: ReportStatus;
  institution: SessionInstitution | null;
  validated_at: ISODateTime | null;
  validated_by: UUID | null;
  rejection_reason: RejectionReason | "";
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
/* Mapa público — contrato anónimo (Sprint 5)                                 */
/* -------------------------------------------------------------------------- */

export type PublicReportStateFilter = "active" | "resolved" | "all";

export type PublicReportStatus =
  "CREADO" | "CLASIFICADO" | "ASIGNADO" | "EN_PROCESO" | "RESUELTO" | "CERRADO";

export type PublicReportPriority = "BAJA" | "MEDIA" | "ALTA";

export interface PublicCategory {
  id: string;
  name: string;
  slug: string;
}

export interface PublicInstitution {
  id: string;
  name: string;
  slug: string;
}

/** Lista blanca exacta expuesta por la API pública; no contiene autor ni ubicación exacta. */
export interface PublicReport {
  id: string;
  category: PublicCategory;
  institution: PublicInstitution | null;
  description: string;
  status: PublicReportStatus;
  priority: PublicReportPriority;
  latitude: number;
  longitude: number;
  created_at: string;
}

/** El contador existe solo en el detalle; el listado público cacheado no lo incluye. */
export interface PublicReportDetailData extends PublicReport {
  confirmation_count: number;
}

export type PublicReportsPage = PaginatedResponse<PublicReport>;

/* -------------------------------------------------------------------------- */
/* Panel ciudadano — contrato privado (Sprint 7)                              */
/* -------------------------------------------------------------------------- */

export interface CitizenSummary {
  reports_by_status: Record<ReportStatus, number>;
  confirmations_performed: number;
  points: {
    pending: number;
    accredited: number;
  };
}

export interface MyReport {
  id: UUID;
  category: PublicCategory;
  institution: PublicInstitution | null;
  description: string;
  status: ReportStatus;
  priority: ReportPriority;
  created_at: ISODateTime;
}

export type MyReportsPage = PaginatedResponse<MyReport>;

export type ConfirmationEligibilityCode =
  "REPORT_NOT_ACTIVE" | "SELF_CONFIRMATION_NOT_ALLOWED" | "ALREADY_CONFIRMED";

export interface ConfirmationEligibility {
  can_confirm: boolean;
  code: ConfirmationEligibilityCode | null;
}

export interface ConfirmReportResponse {
  report_id: UUID;
  confirmed_by_me: true;
  confirmation_count: number;
  reward: {
    impact_points: 10;
    status: "PENDIENTE";
  };
}

/** Lista blanca contextual: no incluye autor, archivos, auditoría ni reward. */
export interface PrivateReportDetail extends MyReport {
  latitude: number;
  longitude: number;
  confirmation_count: number;
  confirmed_by_me: boolean;
  confirmation_eligibility: ConfirmationEligibility;
}

export interface ReportHistoryEntry {
  previous_status: ReportStatus | "";
  new_status: ReportStatus;
  reason: string;
  created_at: ISODateTime;
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
