import { ApiError, apiFetch, apiFetchAuthenticatedNoReplay } from "@/lib/api/client";
import type {
  RewardsSummary,
  RewardsCatalog,
  CatalogEntry,
  LedgerMovement,
  Redemption,
  RedeemResponse,
  PaginatedResponse,
} from "@/types/api";
export type RewardsApiErrorCode =
  | "SESSION_EXPIRED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "RATE_LIMIT"
  | "SERVER_ERROR"
  | "NETWORK_ERROR"
  | "INVALID_RESPONSE"
  | "UNKNOWN_ERROR"
  | "REWARD_INACTIVE"
  | "MONTHLY_LIMIT_REACHED"
  | "INSUFFICIENT_BALANCE";
const messages: Record<RewardsApiErrorCode, string> = {
  SESSION_EXPIRED: "Tu sesión expiró. Ingresá de nuevo.",
  FORBIDDEN: "Necesitás una identidad ciudadana verificada para ver tus recompensas.",
  NOT_FOUND: "No encontramos ese vale o no está disponible para tu cuenta.",
  RATE_LIMIT: "Esperá un momento antes de volver a intentar el canje.",
  SERVER_ERROR: "El servicio no está disponible. Intentá más tarde.",
  NETWORK_ERROR: "No pudimos confirmar el resultado. Revisá tus canjes antes de volver a intentar.",
  INVALID_RESPONSE: "No pudimos procesar la respuesta del servicio.",
  UNKNOWN_ERROR: "No pudimos completar la operación. Intentá de nuevo.",
  REWARD_INACTIVE: "Esta recompensa ya no está disponible.",
  MONTHLY_LIMIT_REACHED: "Ya canjeaste esta recompensa este mes.",
  INSUFFICIENT_BALANCE: "Tu saldo cambió. Revisá los créditos disponibles para canjear.",
};
export class RewardsApiError extends Error {
  constructor(
    public readonly code: RewardsApiErrorCode,
    public readonly context: {
      redemptionId?: string;
      balance?: number;
      missingCredits?: number;
    } = {},
  ) {
    super(messages[code]);
    this.name = "RewardsApiError";
  }
}
function invalidResponse(): never {
  throw new RewardsApiError("INVALID_RESPONSE");
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

function isRedemption(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "id",
      "code",
      "period",
      "credits_spent",
      "simulated",
      "created_at",
      "reward",
      "institution",
    ]) &&
    isUuid(value.id) &&
    typeof value.code === "string" &&
    value.code.length > 0 &&
    isPeriod(value.period) &&
    value.credits_spent === 1000 &&
    value.simulated === true &&
    isDate(value.created_at) &&
    isRewardRef(value.reward) &&
    isInstitution(value.institution)
  );
}
function parseSummary(value: unknown): RewardsSummary {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["impact", "credits_by_institution", "period"]) ||
    !isPeriod(value.period) ||
    !isRecord(value.impact) ||
    !hasExactKeys(value.impact, ["historical", "current_month", "pending"]) ||
    !Object.values(value.impact).every(isInteger) ||
    !Array.isArray(value.credits_by_institution) ||
    !value.credits_by_institution.every(
      (v) =>
        isRecord(v) &&
        hasExactKeys(v, ["institution", "balance"]) &&
        isInstitution(v.institution) &&
        isInteger(v.balance),
    )
  )
    invalidResponse();
  return value as unknown as RewardsSummary;
}
function isCatalogEntry(v: unknown): v is CatalogEntry {
  if (
    !isRecord(v) ||
    !hasExactKeys(v, [
      "reward",
      "institution",
      "balance",
      "can_redeem",
      "blocked_reason",
      "missing_credits",
      "redemption_this_month",
    ]) ||
    !isRecord(v.reward) ||
    !hasExactKeys(v.reward, ["id", "name", "description", "credit_cost", "simulated"]) ||
    !isUuid(v.reward.id) ||
    typeof v.reward.name !== "string" ||
    typeof v.reward.description !== "string" ||
    v.reward.credit_cost !== 1000 ||
    v.reward.simulated !== true ||
    !isInstitution(v.institution) ||
    !isInteger(v.balance) ||
    !isNonNegativeInteger(v.missing_credits) ||
    typeof v.can_redeem !== "boolean"
  )
    return false;
  const r = v.redemption_this_month;
  if (v.can_redeem) return v.blocked_reason === null && v.missing_credits === 0 && r === null;
  if (v.blocked_reason === "INSUFFICIENT_BALANCE") return r === null && v.missing_credits > 0;
  return (
    v.blocked_reason === "MONTHLY_LIMIT_REACHED" &&
    v.missing_credits === 0 &&
    isRecord(r) &&
    hasExactKeys(r, ["id", "code", "created_at"]) &&
    isUuid(r.id) &&
    typeof r.code === "string" &&
    isDate(r.created_at)
  );
}
function parseCatalog(v: unknown): RewardsCatalog {
  if (
    !isRecord(v) ||
    !hasExactKeys(v, ["period", "results"]) ||
    !isPeriod(v.period) ||
    !Array.isArray(v.results) ||
    !v.results.every(isCatalogEntry)
  )
    invalidResponse();
  return v as unknown as RewardsCatalog;
}
function isMovement(v: unknown): boolean {
  return (
    isRecord(v) &&
    hasExactKeys(v, [
      "id",
      "event_type",
      "status",
      "impact_delta",
      "credit_delta",
      "institution",
      "report_id",
      "redemption_id",
      "created_at",
      "credited_at",
      "voided_at",
      "reversed_at",
    ]) &&
    isUuid(v.id) &&
    [
      "REPORT_CREATED",
      "CONFIRMATION_CREATED",
      "VALIDATION_BONUS",
      "RESOLUTION_BONUS",
      "REVERSAL",
      "REDEMPTION",
    ].includes(String(v.event_type)) &&
    ["PENDIENTE", "ACREDITADO", "ANULADO", "REVERTIDO"].includes(String(v.status)) &&
    isInteger(v.impact_delta) &&
    isInteger(v.credit_delta) &&
    (v.institution === null || isInstitution(v.institution)) &&
    [v.report_id, v.redemption_id].every((id) => id === null || isUuid(id)) &&
    isDate(v.created_at) &&
    [v.credited_at, v.voided_at, v.reversed_at].every((d) => d === null || isDate(d))
  );
}
function httpCode(status: number): RewardsApiErrorCode {
  return (
    ({ 401: "SESSION_EXPIRED", 403: "FORBIDDEN", 404: "NOT_FOUND", 429: "RATE_LIMIT" } as const)[
      status as 401
    ] ?? (status >= 500 ? "SERVER_ERROR" : "UNKNOWN_ERROR")
  );
}
async function request<T>(
  action: () => Promise<unknown>,
  parser: (value: unknown) => T,
): Promise<T> {
  try {
    return parser(await action());
  } catch (error) {
    if (error instanceof RewardsApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new RewardsApiError(
      error instanceof ApiError
        ? httpCode(error.status)
        : error instanceof SyntaxError
          ? "INVALID_RESPONSE"
          : error instanceof TypeError
            ? "NETWORK_ERROR"
            : "UNKNOWN_ERROR",
    );
  }
}
const base = "/api/v1/rewards";
export function getRewardsSummary(signal?: AbortSignal) {
  return request(() => apiFetch(`${base}/summary/`, { signal, cache: "no-store" }), parseSummary);
}
export function getRewardsCatalog(signal?: AbortSignal) {
  return request(() => apiFetch(`${base}/catalog/`, { signal, cache: "no-store" }), parseCatalog);
}
export function getLedgerMovements({ page }: { page: number }, signal?: AbortSignal) {
  if (!Number.isInteger(page) || page < 1)
    return Promise.reject(new RewardsApiError("INVALID_RESPONSE"));
  return request(
    () => apiFetch(`${base}/transactions/?page=${page}`, { signal, cache: "no-store" }),
    (v) => parsePage<LedgerMovement>(v, isMovement),
  );
}
export function getMyRedemptions({ page }: { page: number }, signal?: AbortSignal) {
  if (!Number.isInteger(page) || page < 1)
    return Promise.reject(new RewardsApiError("INVALID_RESPONSE"));
  return request(
    () => apiFetch(`${base}/redemptions/?page=${page}`, { signal, cache: "no-store" }),
    (v) => parsePage<Redemption>(v, isRedemption),
  );
}
export async function getRedemption(id: string, signal?: AbortSignal): Promise<Redemption> {
  if (!isUuid(id)) invalidResponse();
  return request(
    () => apiFetch(`${base}/redemptions/${id}/`, { signal, cache: "no-store" }),
    (v) => {
      if (!isRedemption(v) || (v as Redemption).id !== id) invalidResponse();
      return v as Redemption;
    },
  );
}
async function handleError(response: Response): Promise<never> {
  if (response.status !== 409) throw new RewardsApiError(httpCode(response.status));
  const v: unknown = await response.json();
  if (!isRecord(v) || typeof v.detail !== "string") invalidResponse();
  if (v.code === "REWARD_INACTIVE" && hasExactKeys(v, ["detail", "code"]))
    throw new RewardsApiError(v.code);
  if (
    v.code === "MONTHLY_LIMIT_REACHED" &&
    hasExactKeys(v, ["detail", "code", "redemption_id"]) &&
    isUuid(v.redemption_id)
  )
    throw new RewardsApiError(v.code, { redemptionId: v.redemption_id });
  if (
    v.code === "INSUFFICIENT_BALANCE" &&
    hasExactKeys(v, ["detail", "code", "balance", "missing_credits"]) &&
    isInteger(v.balance) &&
    isNonNegativeInteger(v.missing_credits)
  )
    throw new RewardsApiError(v.code, { balance: v.balance, missingCredits: v.missing_credits });
  invalidResponse();
}
export async function redeemReward(rewardId: string): Promise<RedeemResponse> {
  if (!isUuid(rewardId)) invalidResponse();
  return request(
    () =>
      apiFetchAuthenticatedNoReplay(
        `${base}/${rewardId}/redeem/`,
        { method: "POST", cache: "no-store" },
        handleError,
      ),
    (v) => {
      if (
        !isRecord(v) ||
        !hasExactKeys(v, ["redemption", "balance_after"]) ||
        !isRedemption(v.redemption) ||
        !isInteger(v.balance_after) ||
        (v.redemption as Redemption).reward.id !== rewardId
      )
        invalidResponse();
      return v as unknown as RedeemResponse;
    },
  );
}
