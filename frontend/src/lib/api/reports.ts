import type {
  CreateReportInput,
  CreateReportResponse,
  PossibleDuplicateReport,
  PossibleDuplicatesInput,
} from "@/features/reports/create/types";
import { isCreateReportInputValid, isUuid } from "@/features/reports/create/validation";
import { ApiError, apiFetch, apiFetchAuthenticatedNoReplay } from "@/lib/api/client";
import type {
  CitizenSummary,
  ConfirmationEligibilityCode,
  ConfirmReportResponse,
  ConfirmationEligibility,
  MyReport,
  MyReportsPage,
  PrivateReportDetail,
  ReportHistoryEntry,
} from "@/types/api";
import type { ReportPriority, ReportStatus } from "@/types/domain";

export type CreateReportErrorCode =
  | "VALIDATION_ERROR"
  | "SESSION_EXPIRED"
  | "NOT_VERIFIED_OR_FORBIDDEN"
  | "DUPLICATE_PHOTO"
  | "RATE_LIMIT"
  | "SERVICE_UNAVAILABLE"
  | "UNCERTAIN_RESULT"
  | "UNKNOWN_ERROR";

const ERROR_MESSAGES: Record<CreateReportErrorCode, string> = {
  VALIDATION_ERROR: "Revisá los datos del reporte.",
  SESSION_EXPIRED: "Tu sesión expiró. Ingresá de nuevo.",
  NOT_VERIFIED_OR_FORBIDDEN: "Necesitás una identidad ciudadana verificada para crear reportes.",
  DUPLICATE_PHOTO: "Esta fotografía ya fue utilizada en otro reporte.",
  RATE_LIMIT: "Alcanzaste el límite de reportes. Esperá antes de volver a intentar.",
  SERVICE_UNAVAILABLE: "No se pudo guardar el reporte en este momento. Intentá más tarde.",
  UNCERTAIN_RESULT:
    "No pudimos confirmar el resultado. El reporte podría haberse creado; no lo envíes otra vez todavía.",
  UNKNOWN_ERROR: "No se pudo completar la creación del reporte.",
};

export class CreateReportError extends Error {
  constructor(
    public readonly code: CreateReportErrorCode,
    public readonly status?: number,
  ) {
    super(ERROR_MESSAGES[code]);
    this.name = "CreateReportError";
  }
}

export function buildCreateReportFormData(input: CreateReportInput): FormData {
  const body = new FormData();
  body.append("category", input.categoryId);
  body.append("description", input.description);
  body.append("photo", input.photo);
  body.append("lat", String(input.location.latitude));
  body.append("lng", String(input.location.longitude));
  return body;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => keys.includes(key));
}

function parseCreateReportResponse(value: unknown): CreateReportResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["id", "status", "reward"]) ||
    !isRecord(value.reward) ||
    !hasExactKeys(value.reward, ["impact_points", "credits", "status"])
  ) {
    throw new CreateReportError("UNKNOWN_ERROR");
  }
  if (
    !isUuid(value.id) ||
    value.status !== "CREADO" ||
    value.reward.impact_points !== 20 ||
    value.reward.credits !== 20 ||
    value.reward.status !== "PENDIENTE"
  ) {
    throw new CreateReportError("UNKNOWN_ERROR");
  }
  return value as unknown as CreateReportResponse;
}

function createErrorFromHttp(error: ApiError): CreateReportError {
  const byStatus: Partial<Record<number, CreateReportErrorCode>> = {
    400: "VALIDATION_ERROR",
    401: "SESSION_EXPIRED",
    403: "NOT_VERIFIED_OR_FORBIDDEN",
    409: "DUPLICATE_PHOTO",
    429: "RATE_LIMIT",
    503: "SERVICE_UNAVAILABLE",
  };
  return new CreateReportError(byStatus[error.status] ?? "UNKNOWN_ERROR", error.status);
}

/** Crea una vez: preflight de sesión, un único POST y ningún replay posterior a 401. */
export async function createReport(
  input: CreateReportInput,
  signal?: AbortSignal,
): Promise<CreateReportResponse> {
  if (!isCreateReportInputValid(input)) throw new CreateReportError("VALIDATION_ERROR");

  try {
    const response = await apiFetchAuthenticatedNoReplay<unknown>("/api/v1/reports/", {
      method: "POST",
      body: buildCreateReportFormData(input),
      signal,
    });
    return parseCreateReportResponse(response);
  } catch (error) {
    if (error instanceof CreateReportError) throw error;
    if (error instanceof ApiError) throw createErrorFromHttp(error);
    throw new CreateReportError("UNCERTAIN_RESULT");
  }
}

function isPossibleDuplicate(value: unknown): value is PossibleDuplicateReport {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "id",
      "category",
      "institution",
      "description",
      "status",
      "priority",
      "created_at",
    ]) ||
    !isRecord(value.category) ||
    !hasExactKeys(value.category, ["id", "name", "slug"])
  ) {
    return false;
  }
  const institutionIsValid =
    value.institution === null ||
    (isRecord(value.institution) &&
      hasExactKeys(value.institution, ["id", "name", "slug"]) &&
      isUuid(value.institution.id) &&
      typeof value.institution.name === "string" &&
      typeof value.institution.slug === "string");
  return (
    isUuid(value.id) &&
    isUuid(value.category.id) &&
    typeof value.category.name === "string" &&
    typeof value.category.slug === "string" &&
    institutionIsValid &&
    typeof value.description === "string" &&
    ["CREADO", "CLASIFICADO", "ASIGNADO", "EN_PROCESO"].includes(String(value.status)) &&
    ["BAJA", "MEDIA", "ALTA"].includes(String(value.priority)) &&
    typeof value.created_at === "string"
  );
}

/** GET privado, cancelable e idempotente; un fallo nunca altera la posibilidad de crear. */
export async function getPossibleReportDuplicates(
  input: PossibleDuplicatesInput,
  signal?: AbortSignal,
): Promise<PossibleDuplicateReport[]> {
  const query = new URLSearchParams({
    lat: String(input.location.latitude),
    lng: String(input.location.longitude),
    category_id: input.categoryId,
  });
  const response = await apiFetch<unknown>(`/api/v1/reports/possible-duplicates/?${query}`, {
    signal,
  });
  if (!Array.isArray(response) || !response.every(isPossibleDuplicate)) {
    throw new ApiError("La API devolvió una respuesta de duplicados inválida.", 502);
  }
  return response;
}

/* -------------------------------------------------------------------------- */
/* Panel ciudadano — GET privados, cancelables y con parsing estricto         */
/* -------------------------------------------------------------------------- */

export type CitizenPanelApiErrorCode =
  | "SESSION_EXPIRED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "RATE_LIMIT"
  | "SERVER_ERROR"
  | "NETWORK_ERROR"
  | "INVALID_RESPONSE"
  | "UNKNOWN_ERROR";

const PANEL_ERROR_MESSAGES: Record<CitizenPanelApiErrorCode, string> = {
  SESSION_EXPIRED: "Tu sesión expiró. Ingresá de nuevo.",
  FORBIDDEN: "Necesitás una identidad ciudadana verificada para ver el panel.",
  NOT_FOUND: "No pudimos encontrar ese seguimiento o no está disponible para tu cuenta.",
  RATE_LIMIT: "Hiciste demasiadas consultas. Esperá un momento antes de volver a intentar.",
  SERVER_ERROR: "El servicio no está disponible en este momento. Intentá más tarde.",
  NETWORK_ERROR: "No pudimos conectarnos. Revisá tu conexión e intentá de nuevo.",
  INVALID_RESPONSE: "El servicio devolvió una respuesta que no pudimos procesar.",
  UNKNOWN_ERROR: "No se pudo completar la consulta.",
};

export class CitizenPanelApiError extends Error {
  constructor(
    public readonly code: CitizenPanelApiErrorCode,
    public readonly status?: number,
  ) {
    super(PANEL_ERROR_MESSAGES[code]);
    this.name = "CitizenPanelApiError";
  }
}

const REPORT_STATUSES = [
  "CREADO",
  "CLASIFICADO",
  "ASIGNADO",
  "EN_PROCESO",
  "RESUELTO",
  "CERRADO",
  "RECHAZADO",
] as const satisfies readonly ReportStatus[];

const REPORT_PRIORITIES = ["BAJA", "MEDIA", "ALTA"] as const satisfies readonly ReportPriority[];
const ELIGIBILITY_CODES = [
  "REPORT_NOT_ACTIVE",
  "SELF_CONFIRMATION_NOT_ALLOWED",
  "ALREADY_CONFIRMED",
] as const;

function invalidResponse(): never {
  throw new CitizenPanelApiError("INVALID_RESPONSE");
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isIsoDateTime(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "" && !Number.isNaN(Date.parse(value));
}

function isReportStatus(value: unknown): value is ReportStatus {
  return REPORT_STATUSES.includes(value as ReportStatus);
}

function isReportPriority(value: unknown): value is ReportPriority {
  return REPORT_PRIORITIES.includes(value as ReportPriority);
}

function isCategory(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["id", "name", "slug"]) &&
    isUuid(value.id) &&
    typeof value.name === "string" &&
    typeof value.slug === "string"
  );
}

function isInstitution(value: unknown): boolean {
  return value === null || isCategory(value);
}

function isMyReport(value: unknown): value is MyReport {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "id",
      "category",
      "institution",
      "description",
      "status",
      "priority",
      "created_at",
    ]) &&
    isUuid(value.id) &&
    isCategory(value.category) &&
    isInstitution(value.institution) &&
    typeof value.description === "string" &&
    isReportStatus(value.status) &&
    isReportPriority(value.priority) &&
    isIsoDateTime(value.created_at)
  );
}

function isReportsByStatus(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, REPORT_STATUSES) &&
    REPORT_STATUSES.every((status) => isNonNegativeInteger(value[status]))
  );
}

function parseCitizenSummary(value: unknown): CitizenSummary {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["reports_by_status", "confirmations_performed", "points"]) ||
    !isReportsByStatus(value.reports_by_status) ||
    !isNonNegativeInteger(value.confirmations_performed) ||
    !isRecord(value.points) ||
    !hasExactKeys(value.points, ["pending", "accredited"]) ||
    !isNonNegativeInteger(value.points.pending) ||
    !isNonNegativeInteger(value.points.accredited)
  ) {
    invalidResponse();
  }
  return value as unknown as CitizenSummary;
}

function parseMyReportsPage(value: unknown): MyReportsPage {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["count", "next", "previous", "results"]) ||
    !isNonNegativeInteger(value.count) ||
    !(value.next === null || typeof value.next === "string") ||
    !(value.previous === null || typeof value.previous === "string") ||
    !Array.isArray(value.results) ||
    !value.results.every(isMyReport) ||
    value.results.length > value.count
  ) {
    invalidResponse();
  }
  return value as unknown as MyReportsPage;
}

function isEligibility(value: unknown): value is ConfirmationEligibility {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["can_confirm", "code"]) ||
    typeof value.can_confirm !== "boolean" ||
    !(
      value.code === null ||
      ELIGIBILITY_CODES.includes(value.code as (typeof ELIGIBILITY_CODES)[number])
    )
  ) {
    return false;
  }
  return value.can_confirm ? value.code === null : value.code !== null;
}

function parsePrivateReportDetail(value: unknown): PrivateReportDetail {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "id",
      "category",
      "institution",
      "description",
      "status",
      "priority",
      "latitude",
      "longitude",
      "created_at",
      "confirmation_count",
      "confirmed_by_me",
      "confirmation_eligibility",
    ]) ||
    !isMyReport({
      id: value.id,
      category: value.category,
      institution: value.institution,
      description: value.description,
      status: value.status,
      priority: value.priority,
      created_at: value.created_at,
    }) ||
    typeof value.latitude !== "number" ||
    !Number.isFinite(value.latitude) ||
    value.latitude < -90 ||
    value.latitude > 90 ||
    typeof value.longitude !== "number" ||
    !Number.isFinite(value.longitude) ||
    value.longitude < -180 ||
    value.longitude > 180 ||
    !isNonNegativeInteger(value.confirmation_count) ||
    typeof value.confirmed_by_me !== "boolean" ||
    !isEligibility(value.confirmation_eligibility)
  ) {
    invalidResponse();
  }
  return value as unknown as PrivateReportDetail;
}

function parseReportHistory(value: unknown): ReportHistoryEntry[] {
  if (
    !Array.isArray(value) ||
    !value.every(
      (entry) =>
        isRecord(entry) &&
        hasExactKeys(entry, ["previous_status", "new_status", "reason", "created_at"]) &&
        (entry.previous_status === "" || isReportStatus(entry.previous_status)) &&
        isReportStatus(entry.new_status) &&
        typeof entry.reason === "string" &&
        isIsoDateTime(entry.created_at),
    )
  ) {
    invalidResponse();
  }
  return value as ReportHistoryEntry[];
}

function panelErrorFromHttp(error: ApiError): CitizenPanelApiError {
  const byStatus: Partial<Record<number, CitizenPanelApiErrorCode>> = {
    401: "SESSION_EXPIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    429: "RATE_LIMIT",
  };
  const code = error.status >= 500 ? "SERVER_ERROR" : (byStatus[error.status] ?? "UNKNOWN_ERROR");
  return new CitizenPanelApiError(code, error.status);
}

async function panelGet<T>(
  path: string,
  parser: (value: unknown) => T,
  signal?: AbortSignal,
): Promise<T> {
  try {
    return parser(await apiFetch<unknown>(path, { signal }));
  } catch (error) {
    if (error instanceof CitizenPanelApiError) throw error;
    if (error instanceof ApiError) throw panelErrorFromHttp(error);
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof SyntaxError) throw new CitizenPanelApiError("INVALID_RESPONSE");
    if (error instanceof TypeError) throw new CitizenPanelApiError("NETWORK_ERROR");
    throw new CitizenPanelApiError("UNKNOWN_ERROR");
  }
}

export function getCitizenSummary(signal?: AbortSignal): Promise<CitizenSummary> {
  return panelGet("/api/v1/citizen/summary/", parseCitizenSummary, signal);
}

export function getMyReports(
  { page }: { page: number },
  signal?: AbortSignal,
): Promise<MyReportsPage> {
  if (!Number.isInteger(page) || page < 1)
    return Promise.reject(new CitizenPanelApiError("INVALID_RESPONSE"));
  return panelGet(`/api/v1/reports/mine/?page=${page}`, parseMyReportsPage, signal);
}

function requireReportId(id: string): void {
  if (!isUuid(id)) throw new CitizenPanelApiError("INVALID_RESPONSE");
}

export async function getReportDetail(
  id: string,
  signal?: AbortSignal,
): Promise<PrivateReportDetail> {
  requireReportId(id);
  const detail = await panelGet(`/api/v1/reports/${id}/`, parsePrivateReportDetail, signal);
  if (detail.id !== id) throw new CitizenPanelApiError("INVALID_RESPONSE");
  return detail;
}

export function getReportHistory(id: string, signal?: AbortSignal): Promise<ReportHistoryEntry[]> {
  requireReportId(id);
  return panelGet(`/api/v1/reports/${id}/history/`, parseReportHistory, signal);
}

/* -------------------------------------------------------------------------- */
/* Confirmación ciudadana — mutación de una sola ejecución                    */
/* -------------------------------------------------------------------------- */

export type ConfirmReportErrorCode =
  | "VALIDATION_ERROR"
  | "SESSION_EXPIRED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | ConfirmationEligibilityCode
  | "RATE_LIMIT"
  | "SERVER_ERROR"
  | "INVALID_RESPONSE"
  | "UNCERTAIN_RESULT"
  | "UNKNOWN_ERROR";

const CONFIRM_ERROR_MESSAGES: Record<ConfirmReportErrorCode, string> = {
  VALIDATION_ERROR: "La confirmación no pudo procesarse.",
  SESSION_EXPIRED: "Tu sesión expiró. Ingresá de nuevo antes de volver a intentarlo.",
  FORBIDDEN: "No tenés permiso para confirmar este reporte.",
  NOT_FOUND: "La confirmación ya no está disponible.",
  REPORT_NOT_ACTIVE: "Este reporte ya no está activo y no puede confirmarse.",
  SELF_CONFIRMATION_NOT_ALLOWED: "No puedes confirmar tu propio reporte.",
  ALREADY_CONFIRMED: "Ya confirmaste que este problema sigue ahí.",
  RATE_LIMIT: "Alcanzaste el límite temporal de confirmaciones. Intentá más tarde.",
  SERVER_ERROR: "No pudimos registrar la confirmación en este momento.",
  INVALID_RESPONSE:
    "El servicio respondió de forma inesperada. Comprobá el estado antes de intentar otra vez.",
  UNCERTAIN_RESULT:
    "No pudimos confirmar el resultado. Comprobá el estado antes de intentar otra vez.",
  UNKNOWN_ERROR: "No se pudo completar la confirmación.",
};

export class ConfirmReportError extends Error {
  constructor(
    public readonly code: ConfirmReportErrorCode,
    public readonly status?: number,
  ) {
    super(CONFIRM_ERROR_MESSAGES[code]);
    this.name = "ConfirmReportError";
  }
}

function parseConfirmReportResponse(value: unknown, reportId: string): ConfirmReportResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["report_id", "confirmed_by_me", "confirmation_count", "reward"]) ||
    value.report_id !== reportId ||
    !isUuid(value.report_id) ||
    value.confirmed_by_me !== true ||
    !isNonNegativeInteger(value.confirmation_count) ||
    !isRecord(value.reward) ||
    !hasExactKeys(value.reward, ["impact_points", "status"]) ||
    value.reward.impact_points !== 10 ||
    value.reward.status !== "PENDIENTE"
  ) {
    throw new ConfirmReportError("INVALID_RESPONSE");
  }
  return value as unknown as ConfirmReportResponse;
}

async function throwConfirmHttpError(response: Response): Promise<never> {
  if (response.status === 409) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new ConfirmReportError("INVALID_RESPONSE", 409);
    }
    if (
      !isRecord(body) ||
      !hasExactKeys(body, ["detail", "code"]) ||
      typeof body.detail !== "string" ||
      !ELIGIBILITY_CODES.includes(body.code as (typeof ELIGIBILITY_CODES)[number])
    ) {
      throw new ConfirmReportError("INVALID_RESPONSE", 409);
    }
    throw new ConfirmReportError(body.code as ConfirmationEligibilityCode, 409);
  }

  const byStatus: Partial<Record<number, ConfirmReportErrorCode>> = {
    400: "VALIDATION_ERROR",
    401: "SESSION_EXPIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    429: "RATE_LIMIT",
  };
  const code =
    response.status >= 500 ? "SERVER_ERROR" : (byStatus[response.status] ?? "UNKNOWN_ERROR");
  throw new ConfirmReportError(code, response.status);
}

/** Preflight de sesión, un único POST vacío y ningún refresh/replay posterior. */
export async function confirmReport(
  id: string,
  signal?: AbortSignal,
): Promise<ConfirmReportResponse> {
  if (!isUuid(id)) throw new ConfirmReportError("INVALID_RESPONSE");
  try {
    const response = await apiFetchAuthenticatedNoReplay<unknown>(
      `/api/v1/reports/${id}/confirm/`,
      { method: "POST", signal },
      throwConfirmHttpError,
    );
    return parseConfirmReportResponse(response, id);
  } catch (error) {
    if (error instanceof ConfirmReportError) throw error;
    if (error instanceof ApiError) {
      throw new ConfirmReportError(
        error.status === 401 ? "SESSION_EXPIRED" : "UNKNOWN_ERROR",
        error.status,
      );
    }
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof SyntaxError) throw new ConfirmReportError("INVALID_RESPONSE");
    if (error instanceof TypeError) throw new ConfirmReportError("UNCERTAIN_RESULT");
    throw new ConfirmReportError("UNKNOWN_ERROR");
  }
}
