import { ApiError, apiFetch, apiFetchAuthenticatedNoReplay } from "@/lib/api/client";
import type {
  OperatorAiClassification,
  OperatorReport,
  OperatorReportActionResult,
  OperatorReportDetail,
  PaginatedResponse,
  SessionInstitution,
} from "@/types/api";
import type { RejectionReason, ReportPriority, ReportStatus } from "@/types/domain";

export type OperatorConflictCode =
  | "ALREADY_VALIDATED"
  | "ALREADY_REJECTED"
  | "VALIDATION_REVERSAL_REQUIRED"
  | "REPORT_STATE_NOT_ALLOWED"
  | "INSTITUTION_INACTIVE";

export type OperatorApiErrorCode =
  | OperatorConflictCode
  | "INVALID_REJECTION_REASON"
  | "INSTITUTION_OPERATOR_REQUIRED"
  | "REPORT_NOT_FOUND"
  | "SESSION_EXPIRED"
  | "VALIDATION_ERROR"
  | "SERVER_ERROR"
  | "NETWORK_ERROR"
  | "INVALID_RESPONSE";

export class OperatorApiError extends Error {
  constructor(
    public readonly code: OperatorApiErrorCode,
    public readonly status?: number,
  ) {
    super(operatorErrorMessage(code));
    this.name = "OperatorApiError";
  }
}

export function operatorErrorMessage(code: OperatorApiErrorCode): string {
  if (
    code === "ALREADY_VALIDATED" ||
    code === "ALREADY_REJECTED" ||
    code === "VALIDATION_REVERSAL_REQUIRED" ||
    code === "REPORT_STATE_NOT_ALLOWED"
  ) {
    return "El reporte cambió mientras lo revisabas. Actualizamos sus datos.";
  }
  if (code === "INSTITUTION_INACTIVE") {
    return "La institución seleccionada ya no está activa.";
  }
  const messages: Record<Exclude<OperatorApiErrorCode, OperatorConflictCode>, string> = {
    INVALID_REJECTION_REASON: "Seleccioná un motivo de rechazo válido.",
    INSTITUTION_OPERATOR_REQUIRED: "Ya no tenés acceso a esta institución.",
    REPORT_NOT_FOUND: "El reporte no existe o ya no pertenece a esta institución.",
    SESSION_EXPIRED: "Tu sesión expiró.",
    VALIDATION_ERROR: "Revisá los datos enviados.",
    SERVER_ERROR: "No pudimos completar la operación en este momento.",
    NETWORK_ERROR: "No pudimos confirmar el resultado. Actualizá el reporte antes de reintentar.",
    INVALID_RESPONSE: "La API devolvió una respuesta inesperada.",
  };
  return messages[code];
}

const STATUSES: ReportStatus[] = [
  "CREADO",
  "CLASIFICADO",
  "ASIGNADO",
  "EN_PROCESO",
  "RESUELTO",
  "CERRADO",
  "RECHAZADO",
];
const PRIORITIES: ReportPriority[] = ["BAJA", "MEDIA", "ALTA"];
const REASONS: RejectionReason[] = [
  "fuera_de_competencia",
  "duplicado",
  "informacion_insuficiente",
  "contenido_inapropiado",
];
const CONFLICT_CODES: OperatorConflictCode[] = [
  "ALREADY_VALIDATED",
  "ALREADY_REJECTED",
  "VALIDATION_REVERSAL_REQUIRED",
  "REPORT_STATE_NOT_ALLOWED",
  "INSTITUTION_INACTIVE",
];

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}

function uuid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(value);
}

function institution(value: unknown): value is SessionInstitution {
  return (
    record(value) &&
    exact(value, ["id", "name", "slug"]) &&
    uuid(value.id) &&
    typeof value.name === "string" &&
    typeof value.slug === "string"
  );
}

function category(value: unknown): boolean {
  return institution(value);
}

function report(value: unknown): value is OperatorReport {
  return (
    record(value) &&
    exact(value, [
      "id",
      "status",
      "priority",
      "category",
      "institution",
      "effective_read_institution",
      "created_at",
    ]) &&
    uuid(value.id) &&
    STATUSES.includes(value.status as ReportStatus) &&
    PRIORITIES.includes(value.priority as ReportPriority) &&
    category(value.category) &&
    (value.institution === null || institution(value.institution)) &&
    institution(value.effective_read_institution) &&
    typeof value.created_at === "string"
  );
}

function aiClassification(value: unknown): value is OperatorAiClassification {
  if (!record(value)) return false;
  return (
    exact(value, [
      "suggested_category",
      "suggested_institution",
      "suggested_priority",
      "confidence",
      "inconsistency_flag",
      "justification",
      "model_name",
      "requires_human_review",
      "created_at",
    ]) &&
    (value.suggested_category === null || category(value.suggested_category)) &&
    (value.suggested_institution === null || institution(value.suggested_institution)) &&
    typeof value.suggested_priority === "string" &&
    (value.confidence === null || typeof value.confidence === "number") &&
    typeof value.inconsistency_flag === "boolean" &&
    typeof value.justification === "string" &&
    typeof value.model_name === "string" &&
    (value.requires_human_review === null || typeof value.requires_human_review === "boolean") &&
    typeof value.created_at === "string"
  );
}

function detail(value: unknown): value is OperatorReportDetail {
  if (!record(value)) return false;
  const summary = Object.fromEntries(
    Object.entries(value).filter(
      ([key]) =>
        ![
          "description",
          "latitude",
          "longitude",
          "photo_signed_url",
          "confirmation_count",
          "ai_classification",
        ].includes(key),
    ),
  );
  return (
    report(summary) &&
    exact(value, [
      ...Object.keys(summary),
      "description",
      "latitude",
      "longitude",
      "photo_signed_url",
      "confirmation_count",
      "ai_classification",
    ]) &&
    typeof value.description === "string" &&
    typeof value.latitude === "number" &&
    typeof value.longitude === "number" &&
    (value.photo_signed_url === null || typeof value.photo_signed_url === "string") &&
    Number.isInteger(value.confirmation_count) &&
    (value.ai_classification === null || aiClassification(value.ai_classification))
  );
}

function actionResult(value: unknown): value is OperatorReportActionResult {
  return (
    record(value) &&
    exact(value, [
      "id",
      "status",
      "institution",
      "validated_at",
      "validated_by",
      "rejection_reason",
    ]) &&
    uuid(value.id) &&
    STATUSES.includes(value.status as ReportStatus) &&
    (value.institution === null || institution(value.institution)) &&
    (value.validated_at === null || typeof value.validated_at === "string") &&
    (value.validated_by === null || uuid(value.validated_by)) &&
    (value.rejection_reason === "" || REASONS.includes(value.rejection_reason as RejectionReason))
  );
}

function contextQuery(institutionId: string, page?: number): string {
  if (!uuid(institutionId)) throw new OperatorApiError("INVALID_RESPONSE");
  if (page !== undefined && (!Number.isInteger(page) || page < 1)) {
    throw new OperatorApiError("INVALID_RESPONSE");
  }
  const params = new URLSearchParams({ institution_id: institutionId });
  if (page !== undefined) params.set("page", String(page));
  return params.toString();
}

export async function getOperatorReports(
  institutionId: string,
  page = 1,
): Promise<PaginatedResponse<OperatorReport>> {
  try {
    const value = await apiFetch<unknown>(
      `/api/v1/operator/reports/?${contextQuery(institutionId, page)}`,
    );
    if (
      !record(value) ||
      !exact(value, ["count", "next", "previous", "results"]) ||
      !Number.isInteger(value.count) ||
      !(value.next === null || typeof value.next === "string") ||
      !(value.previous === null || typeof value.previous === "string") ||
      !Array.isArray(value.results) ||
      !value.results.every(report)
    ) {
      throw new OperatorApiError("INVALID_RESPONSE");
    }
    return value as unknown as PaginatedResponse<OperatorReport>;
  } catch (error) {
    throw normalizeReadError(error);
  }
}

export async function getOperatorReport(
  id: string,
  institutionId: string,
): Promise<OperatorReportDetail> {
  if (!uuid(id)) throw new OperatorApiError("INVALID_RESPONSE");
  try {
    const value = await apiFetch<unknown>(
      `/api/v1/operator/reports/${id}/?${contextQuery(institutionId)}`,
    );
    if (!detail(value) || value.id !== id) throw new OperatorApiError("INVALID_RESPONSE");
    return value;
  } catch (error) {
    throw normalizeReadError(error);
  }
}

function normalizeReadError(error: unknown): OperatorApiError {
  if (error instanceof OperatorApiError) return error;
  if (error instanceof ApiError) {
    if (error.status === 401) return new OperatorApiError("SESSION_EXPIRED", 401);
    if (error.status === 403) return new OperatorApiError("INSTITUTION_OPERATOR_REQUIRED", 403);
    if (error.status === 404) return new OperatorApiError("REPORT_NOT_FOUND", 404);
    return new OperatorApiError("SERVER_ERROR", error.status);
  }
  return new OperatorApiError(error instanceof TypeError ? "NETWORK_ERROR" : "INVALID_RESPONSE");
}

async function mutationError(response: Response): Promise<never> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new OperatorApiError("INVALID_RESPONSE", response.status);
  }
  const code = record(payload) && typeof payload.code === "string" ? payload.code : null;
  if (response.status === 409 && CONFLICT_CODES.includes(code as OperatorConflictCode)) {
    throw new OperatorApiError(code as OperatorConflictCode, 409);
  }
  if (response.status === 400 && code === "INVALID_REJECTION_REASON") {
    throw new OperatorApiError(code, 400);
  }
  if (response.status === 403 && code === "INSTITUTION_OPERATOR_REQUIRED") {
    throw new OperatorApiError(code, 403);
  }
  if (response.status === 404 && code === "REPORT_NOT_FOUND") {
    throw new OperatorApiError(code, 404);
  }
  if (response.status === 401) throw new OperatorApiError("SESSION_EXPIRED", 401);
  if (response.status === 400) throw new OperatorApiError("VALIDATION_ERROR", 400);
  if (response.status >= 500) throw new OperatorApiError("SERVER_ERROR", response.status);
  throw new OperatorApiError("INVALID_RESPONSE", response.status);
}

async function mutate(
  id: string,
  institutionId: string,
  action: "validate" | "reject",
  reason?: RejectionReason,
): Promise<OperatorReportActionResult> {
  if (!uuid(id)) throw new OperatorApiError("INVALID_RESPONSE");
  try {
    const init: RequestInit = { method: "POST" };
    if (action === "reject") {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify({ reason });
    }
    const value = await apiFetchAuthenticatedNoReplay<unknown>(
      `/api/v1/operator/reports/${id}/${action}/?${contextQuery(institutionId)}`,
      init,
      mutationError,
    );
    if (!actionResult(value) || value.id !== id) throw new OperatorApiError("INVALID_RESPONSE");
    return value;
  } catch (error) {
    if (error instanceof OperatorApiError) throw error;
    if (error instanceof ApiError && error.status === 401) {
      throw new OperatorApiError("SESSION_EXPIRED", 401);
    }
    throw new OperatorApiError(error instanceof TypeError ? "NETWORK_ERROR" : "INVALID_RESPONSE");
  }
}

export const validateOperatorReport = (id: string, institutionId: string) =>
  mutate(id, institutionId, "validate");

export const rejectOperatorReport = (id: string, institutionId: string, reason: RejectionReason) =>
  mutate(id, institutionId, "reject", reason);
