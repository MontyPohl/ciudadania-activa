import type {
  CreateReportInput,
  CreateReportResponse,
  PossibleDuplicateReport,
  PossibleDuplicatesInput,
} from "@/features/reports/create/types";
import { isCreateReportInputValid, isUuid } from "@/features/reports/create/validation";
import { ApiError, apiFetch, apiFetchAuthenticatedNoReplay } from "@/lib/api/client";

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
