import type {
  ActorType,
  ISODateTime,
  RankingVisibility,
  RejectionReason,
  RewardEventType,
  RewardTxStatus,
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
  cycle: number;
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
  validated_at: ISODateTime | null;
  resolved_at: ISODateTime | null;
  closed_at: ISODateTime | null;
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

export interface ReportLifecycleResult {
  id: UUID;
  status: ReportStatus;
  cycle: number;
  institution: SessionInstitution;
  validated_at: ISODateTime | null;
  resolved_at: ISODateTime | null;
  closed_at: ISODateTime | null;
}

export interface ResolutionEvidence {
  id: UUID;
  cycle: number;
  description: string;
  created_at: ISODateTime;
  signed_url: string;
  expires_in: number;
}

export interface AdminAuditLog {
  id: UUID;
  actor_user_id: UUID | null;
  actor_type: ActorType;
  action: string;
  payload: Record<string, unknown>;
  created_at: ISODateTime;
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

export interface ResolutionBonus {
  impact_points: number;
  credits: number;
  status: RewardTxStatus;
  cycle: number;
}

/** Lista blanca contextual: no incluye autor, archivos ni auditoría. */
export interface PrivateReportDetail extends MyReport {
  cycle: number;
  validated_at: ISODateTime | null;
  resolved_at: ISODateTime | null;
  closed_at: ISODateTime | null;
  latitude: number;
  longitude: number;
  confirmation_count: number;
  confirmed_by_me: boolean;
  confirmation_eligibility: ConfirmationEligibility;
  resolution_bonus: ResolutionBonus | null;
}

export interface ReportHistoryEntry {
  previous_status: ReportStatus | "";
  new_status: ReportStatus;
  cycle: number;
  reason: string;
  created_at: ISODateTime;
}

export type ReportHistoryPage = PaginatedResponse<ReportHistoryEntry>;

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

/* -------------------------------------------------------------------------- */
/* Recompensas y ranking — Sprint 12                                          */
/* -------------------------------------------------------------------------- */

export type RewardPeriod = string; // "YYYY-MM"

export interface RewardPublic {
  id: UUID;
  name: string;
  description: string;
  credit_cost: 1000;
  simulated: true;
}
export interface Redemption {
  id: UUID;
  code: string;
  period: RewardPeriod;
  credits_spent: 1000;
  simulated: true;
  created_at: ISODateTime;
  reward: { id: UUID; name: string };
  institution: SessionInstitution;
}
export interface RewardsSummary {
  impact: { historical: number; current_month: number; pending: number };
  credits_by_institution: Array<{ institution: SessionInstitution; balance: number }>;
  period: RewardPeriod;
}
export interface LedgerMovement {
  id: UUID;
  event_type: RewardEventType;
  status: RewardTxStatus;
  impact_delta: number;
  credit_delta: number;
  institution: SessionInstitution | null;
  report_id: UUID | null;
  redemption_id: UUID | null;
  created_at: ISODateTime;
  credited_at: ISODateTime | null;
  voided_at: ISODateTime | null;
  reversed_at: ISODateTime | null;
}
export type RewardBlockedReason = "MONTHLY_LIMIT_REACHED" | "INSUFFICIENT_BALANCE";
export interface CatalogEntry {
  reward: RewardPublic;
  institution: SessionInstitution;
  balance: number;
  can_redeem: boolean;
  blocked_reason: RewardBlockedReason | null;
  missing_credits: number;
  redemption_this_month: { id: UUID; code: string; created_at: ISODateTime } | null;
}
export interface RewardsCatalog {
  period: RewardPeriod;
  results: CatalogEntry[];
}
export interface RedeemResponse {
  redemption: Redemption;
  balance_after: number;
}
export type RedeemConflictCode =
  "REWARD_INACTIVE" | "MONTHLY_LIMIT_REACHED" | "INSUFFICIENT_BALANCE";

export interface OperatorReward {
  id: UUID;
  institution: SessionInstitution;
  name: string;
  description: string;
  active: boolean;
  credit_cost: 1000;
  simulated: true;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}
export interface OperatorRewardContext {
  institution: SessionInstitution;
  reward: OperatorReward | null;
}
export interface OperatorRedemption {
  id: UUID;
  code: string;
  period: RewardPeriod;
  created_at: ISODateTime;
  reward: { id: UUID; name: string };
  citizen_display: string;
}

export interface MonthlyRanking {
  period: RewardPeriod;
  entries: Array<{
    position: number;
    display_name: string;
    points: number;
    is_private: boolean;
    is_me: boolean;
  }>;
}

// ---------------------------------------------------------------------------
// Sprint 13: métricas institucionales y transparencia (contrato S13 §2–§6)
// ---------------------------------------------------------------------------

/** Mes local `YYYY-MM` (America/Asuncion). */
export type MetricsPeriod = string;

export interface SlaMetrics {
  hours: number | null;
  configured: boolean;
  within: number | null;
  breached: number | null;
  compliance_rate: number | null;
  open_over_sla: number | null;
}

export interface MetricsSeriesPoint {
  date: string;
  received: number;
  resolved: number;
}

export interface MetricsTrendPoint {
  period: MetricsPeriod;
  received: number;
  resolved: number;
}

export interface InstitutionMetricsRow {
  institution: SessionInstitution;
  received: number;
  validated: number;
  rejected: number;
  resolved: number;
  median_hours: number | null;
  sla: { hours: number | null; configured: boolean; compliance_rate: number | null };
}

/** Respuesta de `GET /api/v1/operator/metrics/` y `GET /api/v1/admin/metrics/`. */
export interface InstitutionMetrics {
  scope: "institution" | "all";
  institution: SessionInstitution | null;
  period: MetricsPeriod;
  period_start: ISODateTime;
  period_end: ISODateTime;
  is_current_period: boolean;
  generated_at: ISODateTime;
  reports: {
    received: number;
    by_status: Record<ReportStatus, number>;
    by_priority: Record<ReportPriority, number>;
    by_category: Array<{ category: OperatorCategory; count: number }>;
    resolution_rate: number | null;
  };
  decisions: {
    validated: number;
    rejected: number;
    validation_rate: number | null;
    rejection_rate: number | null;
  };
  resolution: { resolved: number; mean_hours: number | null; median_hours: number | null };
  sla: SlaMetrics;
  confirmations: { count: number };
  incentives: {
    points_credited: number;
    credits_issued: number;
    credits_reverted: number;
    redemptions: number;
    credits_redeemed: number;
  };
  series: { granularity: "day"; points: MetricsSeriesPoint[] };
  trend: MetricsTrendPoint[];
  by_institution: InstitutionMetricsRow[] | null;
}

/** Celdas públicas que pueden suprimirse por tener menos de `min_cell_size` casos. */
export type PublicSuppressible =
  | "confirmations"
  | "redemptions"
  | "credits_issued"
  | "resolution_rate"
  | "median_resolution_hours"
  | "sla.compliance_rate";

export interface PublicMetricsCells {
  received: number;
  validated: number;
  rejected: number;
  resolved: number;
  resolution_rate: number | null;
  median_resolution_hours: number | null;
  confirmations: number | null;
  redemptions: number | null;
  credits_issued: number | null;
  suppressed: PublicSuppressible[];
}

export interface PublicInstitutionMetrics extends PublicMetricsCells {
  institution: SessionInstitution;
  sla: { configured: boolean; hours: number | null; compliance_rate: number | null };
}

export interface PublicTrendPoint {
  period: MetricsPeriod;
  received: number | null;
  resolved: number | null;
  suppressed: ("received" | "resolved")[];
}

export interface PublicCategoryCount {
  category: OperatorCategory;
  received: number | null;
  suppressed: "received"[];
}

/** Respuesta de `GET /api/v1/public/metrics/` (S15-B1). */
export interface PublicMetrics {
  window: { from: string; to: string; months: number };
  generated_at: ISODateTime;
  min_cell_size: number;
  totals: PublicMetricsCells;
  institutions: PublicInstitutionMetrics[];
  trend: PublicTrendPoint[];
  current_month_by_category: PublicCategoryCount[];
}
