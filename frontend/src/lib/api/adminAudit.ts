import { ApiError, apiFetch, apiFetchAuthenticatedNoReplay } from "@/lib/api/client";
import type {
  AdminAuditLog,
  PaginatedResponse,
  ReportLifecycleResult,
  SessionInstitution,
} from "@/types/api";
import type { ActorType, ReportStatus } from "@/types/domain";

export interface AuditFilters {
  action?: string;
  actorType?: ActorType;
  actorUserId?: string;
  reportId?: string;
  institutionId?: string;
  createdFrom?: string;
  createdTo?: string;
  page?: number;
}

export type AdminAuditErrorCode =
  | "INVALID_REQUEST"
  | "ADMIN_REQUIRED"
  | "REPORT_NOT_FOUND"
  | "STALE_REPORT_CYCLE"
  | "REPORT_STATE_NOT_ALLOWED"
  | "SESSION_EXPIRED"
  | "SERVER_ERROR"
  | "NETWORK_ERROR"
  | "INVALID_RESPONSE"
  | "UNCERTAIN_RESULT";

const ERROR_MESSAGES: Record<AdminAuditErrorCode, string> = {
  INVALID_REQUEST: "Revisá los filtros o los datos enviados.",
  ADMIN_REQUIRED: "Tu cuenta ya no tiene acceso administrativo.",
  REPORT_NOT_FOUND: "No se encontró el reporte solicitado.",
  STALE_REPORT_CYCLE: "El reporte cambió de ciclo. Actualizamos la auditoría para que lo revises.",
  REPORT_STATE_NOT_ALLOWED: "El estado actual del reporte ya no admite esta reversión.",
  SESSION_EXPIRED: "Tu sesión expiró.",
  SERVER_ERROR: "No pudimos consultar la auditoría en este momento.",
  NETWORK_ERROR: "No pudimos conectarnos. Revisá tu conexión e intentá de nuevo.",
  INVALID_RESPONSE: "El servicio devolvió una respuesta inesperada.",
  UNCERTAIN_RESULT:
    "No pudimos confirmar el resultado. Actualizá la auditoría antes de volver a intentar.",
};

export class AdminAuditError extends Error {
  constructor(
    public readonly code: AdminAuditErrorCode,
    public readonly status?: number,
  ) {
    super(ERROR_MESSAGES[code]);
    this.name = "AdminAuditError";
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}

function uuid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(value);
}

function isoDateTime(value: unknown): value is string {
  return typeof value === "string" && value !== "" && !Number.isNaN(Date.parse(value));
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

function auditLog(value: unknown): value is AdminAuditLog {
  return (
    record(value) &&
    exact(value, ["id", "actor_user_id", "actor_type", "action", "payload", "created_at"]) &&
    uuid(value.id) &&
    (value.actor_user_id === null || uuid(value.actor_user_id)) &&
    ["human", "ai", "system"].includes(String(value.actor_type)) &&
    typeof value.action === "string" &&
    record(value.payload) &&
    isoDateTime(value.created_at)
  );
}

function auditPage(value: unknown): value is PaginatedResponse<AdminAuditLog> {
  return (
    record(value) &&
    exact(value, ["count", "next", "previous", "results"]) &&
    Number.isInteger(value.count) &&
    Number(value.count) >= 0 &&
    (value.next === null || typeof value.next === "string") &&
    (value.previous === null || typeof value.previous === "string") &&
    Array.isArray(value.results) &&
    value.results.every(auditLog)
  );
}

function lifecycleResult(value: unknown): value is ReportLifecycleResult {
  const statuses: ReportStatus[] = ["CLASIFICADO"];
  return (
    record(value) &&
    exact(value, [
      "id",
      "status",
      "cycle",
      "institution",
      "validated_at",
      "resolved_at",
      "closed_at",
    ]) &&
    uuid(value.id) &&
    statuses.includes(value.status as ReportStatus) &&
    Number.isInteger(value.cycle) &&
    Number(value.cycle) >= 1 &&
    institution(value.institution) &&
    value.validated_at === null &&
    value.resolved_at === null &&
    value.closed_at === null
  );
}

function filterQuery(filters: AuditFilters): string {
  const params = new URLSearchParams();
  if (filters.action) params.set("action", filters.action);
  if (filters.actorType) params.set("actor_type", filters.actorType);
  if (filters.actorUserId) params.set("actor_user_id", filters.actorUserId);
  if (filters.reportId) params.set("report_id", filters.reportId);
  if (filters.institutionId) params.set("institution_id", filters.institutionId);
  if (filters.createdFrom) params.set("created_from", filters.createdFrom);
  if (filters.createdTo) params.set("created_to", filters.createdTo);
  params.set("page", String(filters.page ?? 1));
  return params.toString();
}

function readError(error: unknown): AdminAuditError {
  if (error instanceof AdminAuditError) return error;
  if (error instanceof ApiError) {
    if (error.status === 400) return new AdminAuditError("INVALID_REQUEST", 400);
    if (error.status === 401) return new AdminAuditError("SESSION_EXPIRED", 401);
    if (error.status === 403) return new AdminAuditError("ADMIN_REQUIRED", 403);
    if (error.status >= 500) return new AdminAuditError("SERVER_ERROR", error.status);
  }
  return new AdminAuditError(error instanceof TypeError ? "NETWORK_ERROR" : "INVALID_RESPONSE");
}

export async function getAuditLogs(
  filters: AuditFilters,
): Promise<PaginatedResponse<AdminAuditLog>> {
  try {
    const value = await apiFetch<unknown>(`/api/v1/admin/audit-logs/?${filterQuery(filters)}`);
    if (!auditPage(value)) throw new AdminAuditError("INVALID_RESPONSE");
    return value;
  } catch (error) {
    throw readError(error);
  }
}

async function reversalError(response: Response): Promise<never> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new AdminAuditError("INVALID_RESPONSE", response.status);
  }
  const code = record(payload) && typeof payload.code === "string" ? payload.code : null;
  const known: AdminAuditErrorCode[] = [
    "INVALID_REQUEST",
    "ADMIN_REQUIRED",
    "REPORT_NOT_FOUND",
    "STALE_REPORT_CYCLE",
    "REPORT_STATE_NOT_ALLOWED",
  ];
  if (code !== null && known.includes(code as AdminAuditErrorCode)) {
    throw new AdminAuditError(code as AdminAuditErrorCode, response.status);
  }
  if (response.status === 401) throw new AdminAuditError("SESSION_EXPIRED", 401);
  if (response.status >= 500) throw new AdminAuditError("SERVER_ERROR", response.status);
  throw new AdminAuditError("INVALID_RESPONSE", response.status);
}

export async function revertReportValidation(input: {
  reportId: string;
  cycle: number;
  reason: string;
}): Promise<ReportLifecycleResult> {
  if (!uuid(input.reportId) || !Number.isInteger(input.cycle) || input.cycle < 1) {
    throw new AdminAuditError("INVALID_RESPONSE");
  }
  try {
    const value = await apiFetchAuthenticatedNoReplay<unknown>(
      `/api/v1/admin/reports/${input.reportId}/revert-validation/`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: input.reason, cycle: input.cycle }),
      },
      reversalError,
    );
    if (!lifecycleResult(value) || value.id !== input.reportId || value.cycle !== input.cycle) {
      throw new AdminAuditError("INVALID_RESPONSE");
    }
    return value;
  } catch (error) {
    if (error instanceof AdminAuditError) throw error;
    if (error instanceof ApiError && error.status === 401) {
      throw new AdminAuditError("SESSION_EXPIRED", 401);
    }
    throw new AdminAuditError(error instanceof TypeError ? "NETWORK_ERROR" : "UNCERTAIN_RESULT");
  }
}
