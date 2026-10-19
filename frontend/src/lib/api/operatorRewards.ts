import { ApiError, apiFetch, apiFetchAuthenticatedNoReplay } from "@/lib/api/client";
import type {
  OperatorReward,
  OperatorRewardContext,
  OperatorRedemption,
  PaginatedResponse,
} from "@/types/api";
export type OperatorRewardInput = Pick<OperatorReward, "name" | "description" | "active">;
export class OperatorRewardsApiError extends Error {
  constructor(public readonly code: string) {
    super(
      code === "REWARD_ALREADY_EXISTS"
        ? "Tu institución ya tiene una recompensa. Recargá para editarla."
        : code === "FORBIDDEN"
          ? "No tenés permiso para gestionar esta institución."
          : code === "SESSION_EXPIRED"
            ? "Tu sesión expiró. Ingresá de nuevo."
            : code === "VALIDATION_ERROR"
              ? "Revisá el nombre y la descripción."
              : code === "NETWORK_ERROR"
                ? "No pudimos confirmar el resultado. Recargá antes de volver a guardar."
                : code === "RATE_LIMIT"
                  ? "Esperá un momento antes de volver a intentar."
                  : "No pudimos completar la operación. Intentá de nuevo.",
    );
    this.name = "OperatorRewardsApiError";
  }
}
function invalidResponse(): never {
  throw new OperatorRewardsApiError("INVALID_RESPONSE");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}
function isUuid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
  );
}
function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value);
}
function isNonNegativeInteger(value: unknown): value is number {
  return isInteger(value) && value >= 0;
}
function isDate(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}
function isPeriod(value: unknown): boolean {
  return typeof value === "string" && /^\d{4}-(0[1-9]|1[0-2])$/.test(value);
}
function isInstitution(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["id", "name", "slug"]) &&
    isUuid(value.id) &&
    typeof value.name === "string" &&
    typeof value.slug === "string"
  );
}
function isRewardRef(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["id", "name"]) &&
    isUuid(value.id) &&
    typeof value.name === "string"
  );
}
function parsePage<T>(value: unknown, guard: (value: unknown) => boolean): PaginatedResponse<T> {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["count", "next", "previous", "results"]) ||
    !isNonNegativeInteger(value.count) ||
    !(value.next === null || typeof value.next === "string") ||
    !(value.previous === null || typeof value.previous === "string") ||
    !Array.isArray(value.results) ||
    value.results.length > 20 ||
    value.results.length > value.count ||
    !value.results.every(guard)
  )
    invalidResponse();
  return value as unknown as PaginatedResponse<T>;
}

function isReward(v: unknown): v is OperatorReward {
  return (
    isRecord(v) &&
    hasExactKeys(v, [
      "id",
      "institution",
      "name",
      "description",
      "active",
      "credit_cost",
      "simulated",
      "created_at",
      "updated_at",
    ]) &&
    isUuid(v.id) &&
    isInstitution(v.institution) &&
    typeof v.name === "string" &&
    typeof v.description === "string" &&
    typeof v.active === "boolean" &&
    v.credit_cost === 1000 &&
    v.simulated === true &&
    isDate(v.created_at) &&
    isDate(v.updated_at)
  );
}
function isRedemption(v: unknown): boolean {
  return (
    isRecord(v) &&
    hasExactKeys(v, ["id", "code", "period", "created_at", "reward", "citizen_display"]) &&
    isUuid(v.id) &&
    typeof v.code === "string" &&
    isPeriod(v.period) &&
    isDate(v.created_at) &&
    isRewardRef(v.reward) &&
    typeof v.citizen_display === "string"
  );
}
function path(institutionId: string, suffix = "", page?: number): string {
  if (!isUuid(institutionId) || (page !== undefined && (!Number.isInteger(page) || page < 1)))
    invalidResponse();
  const query = new URLSearchParams({ institution_id: institutionId });
  if (page !== undefined) query.set("page", String(page));
  return `/api/v1/operator/rewards/${suffix}?${query}`;
}
async function handleError(response: Response): Promise<never> {
  if (response.status === 409) {
    const v: unknown = await response.json();
    if (
      !isRecord(v) ||
      v.code !== "REWARD_ALREADY_EXISTS" ||
      !isUuid(v.reward_id) ||
      !Object.keys(v).every((k) => ["detail", "code", "reward_id"].includes(k))
    )
      invalidResponse();
    throw new OperatorRewardsApiError("REWARD_ALREADY_EXISTS");
  }
  throw new ApiError("Error de recompensas", response.status);
}
async function request<T>(action: () => Promise<unknown>, parser: (v: unknown) => T): Promise<T> {
  try {
    return parser(await action());
  } catch (error) {
    if (error instanceof OperatorRewardsApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    const codes: Record<number, string> = {
      400: "VALIDATION_ERROR",
      401: "SESSION_EXPIRED",
      403: "FORBIDDEN",
      404: "NOT_FOUND",
      429: "RATE_LIMIT",
    };
    throw new OperatorRewardsApiError(
      error instanceof ApiError
        ? (codes[error.status] ?? "SERVER_ERROR")
        : error instanceof SyntaxError
          ? "INVALID_RESPONSE"
          : error instanceof TypeError
            ? "NETWORK_ERROR"
            : "UNKNOWN_ERROR",
    );
  }
}
export function getOperatorReward(
  institutionId: string,
  signal?: AbortSignal,
): Promise<OperatorRewardContext> {
  return request(
    () => apiFetch(path(institutionId), { signal, cache: "no-store" }),
    (v) => {
      if (
        !isRecord(v) ||
        !hasExactKeys(v, ["institution", "reward"]) ||
        !isInstitution(v.institution) ||
        (v.institution as { id: string }).id !== institutionId ||
        !(v.reward === null || (isReward(v.reward) && v.reward.institution.id === institutionId))
      )
        invalidResponse();
      return v as unknown as OperatorRewardContext;
    },
  );
}
function save(
  institutionId: string,
  id: string | null,
  input: Partial<OperatorRewardInput>,
): Promise<OperatorReward> {
  if (id !== null && !isUuid(id)) invalidResponse();
  const body: Partial<OperatorRewardInput> = {};
  if (input.name !== undefined) body.name = input.name;
  if (input.description !== undefined) body.description = input.description;
  if (input.active !== undefined) body.active = input.active;
  return request(
    () =>
      apiFetchAuthenticatedNoReplay(
        path(institutionId, id ? `${id}/` : ""),
        {
          method: id ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          cache: "no-store",
        },
        handleError,
      ),
    (v) => {
      if (!isReward(v) || v.institution.id !== institutionId || (id !== null && v.id !== id))
        invalidResponse();
      return v;
    },
  );
}
export function createOperatorReward(institutionId: string, input: OperatorRewardInput) {
  return save(institutionId, null, input);
}
export function updateOperatorReward(
  institutionId: string,
  id: string,
  patch: Partial<OperatorRewardInput>,
) {
  return save(institutionId, id, patch);
}
export function getOperatorRedemptions(
  institutionId: string,
  { page }: { page: number },
  signal?: AbortSignal,
) {
  return request(
    () => apiFetch(path(institutionId, "redemptions/", page), { signal, cache: "no-store" }),
    (v) => parsePage<OperatorRedemption>(v, isRedemption),
  );
}
