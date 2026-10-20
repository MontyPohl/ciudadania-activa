import { ApiError, apiFetch } from "@/lib/api/client";
import type { InstitutionMetrics, OperatorCategory } from "@/types/api";

type MetricsErrorCode =
  | "SESSION_EXPIRED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "BAD_REQUEST"
  | "RATE_LIMIT"
  | "SERVER_ERROR"
  | "NETWORK_ERROR"
  | "INVALID_RESPONSE"
  | "UNKNOWN_ERROR";
export class MetricsApiError extends Error {
  constructor(public readonly code: MetricsErrorCode) {
    super(
      code === "SESSION_EXPIRED"
        ? "Tu sesión expiró. Ingresá de nuevo."
        : code === "FORBIDDEN"
          ? "No tenés acceso a las métricas de esta institución."
          : code === "BAD_REQUEST"
            ? "Elegí la institución y un mes válido para consultar las métricas."
            : code === "RATE_LIMIT"
              ? "Esperá un momento antes de volver a intentar."
              : "No pudimos cargar las métricas. Intentá de nuevo.",
    );
    this.name = "MetricsApiError";
  }
}
function invalidResponse(): never {
  throw new MetricsApiError("INVALID_RESPONSE");
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}
function object(value: unknown, fields: Record<string, (v: unknown) => boolean>): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, Object.keys(fields)) &&
    Object.entries(fields).every(([key, guard]) => guard(value[key]))
  );
}
function count(v: unknown): v is number {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
}
function hours(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0;
}
function rate(v: unknown): boolean {
  return hours(v) && v <= 1;
}
const nullable = (guard: (v: unknown) => boolean) => (v: unknown) => v === null || guard(v);
const list = (guard: (v: unknown) => boolean) => (v: unknown) => Array.isArray(v) && v.every(guard);
const boolean = (v: unknown) => typeof v === "boolean";
function isUuid(v: unknown): v is string {
  return (
    typeof v === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(v)
  );
}
function isRef(v: unknown): boolean {
  return object(v, {
    id: isUuid,
    name: (v) => typeof v === "string",
    slug: (v) => typeof v === "string",
  });
}
function isDate(v: unknown): v is string {
  return (
    typeof v === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(v) &&
    !Number.isNaN(Date.parse(v)) &&
    new Date(v).toISOString().slice(0, 10) === v
  );
}
function isDateTime(v: unknown): boolean {
  return (
    typeof v === "string" &&
    /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v) &&
    !Number.isNaN(Date.parse(v))
  );
}
function isPeriod(v: unknown): v is string {
  return typeof v === "string" && /^\d{4}-(0[1-9]|1[0-2])$/.test(v);
}

function isCategory(v: unknown): v is OperatorCategory {
  return isRef(v);
}
const briefSla = (v: unknown) =>
  object(v, { hours: nullable(hours), configured: boolean, compliance_rate: nullable(rate) });
const institutionRow = (v: unknown) =>
  object(v, {
    institution: isRef,
    received: count,
    validated: count,
    rejected: count,
    resolved: count,
    median_hours: nullable(hours),
    sla: briefSla,
  });
function parseMetrics(v: unknown): InstitutionMetrics {
  if (
    !object(v, {
      scope: (v) => v === "institution" || v === "all",
      institution: nullable(isRef),
      period: isPeriod,
      period_start: isDateTime,
      period_end: isDateTime,
      is_current_period: boolean,
      generated_at: isDateTime,
      reports: (v) =>
        object(v, {
          received: count,
          by_status: (v) =>
            object(v, {
              CREADO: count,
              CLASIFICADO: count,
              ASIGNADO: count,
              EN_PROCESO: count,
              RESUELTO: count,
              CERRADO: count,
              RECHAZADO: count,
            }),
          by_priority: (v) => object(v, { ALTA: count, MEDIA: count, BAJA: count }),
          by_category: list((v) =>
            object(v, { category: isCategory, count: (v) => count(v) && v > 0 }),
          ),
          resolution_rate: nullable(rate),
        }),
      decisions: (v) =>
        object(v, {
          validated: count,
          rejected: count,
          validation_rate: nullable(rate),
          rejection_rate: nullable(rate),
        }),
      resolution: (v) =>
        object(v, { resolved: count, mean_hours: nullable(hours), median_hours: nullable(hours) }),
      sla: (v) =>
        object(v, {
          hours: nullable(hours),
          configured: boolean,
          within: nullable(count),
          breached: nullable(count),
          compliance_rate: nullable(rate),
          open_over_sla: nullable(count),
        }),
      confirmations: (v) => object(v, { count }),
      incentives: (v) =>
        object(v, {
          points_credited: count,
          credits_issued: count,
          credits_reverted: count,
          redemptions: count,
          credits_redeemed: count,
        }),
      series: (v) =>
        object(v, {
          granularity: (v) => v === "day",
          points: list((v) => object(v, { date: isDate, received: count, resolved: count })),
        }),
      trend: (v) =>
        Array.isArray(v) &&
        v.length === 6 &&
        v.every((v) => object(v, { period: isPeriod, received: count, resolved: count })),
      by_institution: nullable(list(institutionRow)),
    })
  )
    invalidResponse();
  const data = v as InstitutionMetrics;
  if (
    data.scope === "institution"
      ? data.institution === null || data.by_institution !== null
      : data.institution !== null || data.by_institution === null
  )
    invalidResponse();
  return data;
}
interface MetricsQuery {
  institutionId?: string;
  period: string;
}
async function request(
  role: "operator" | "admin",
  { institutionId, period }: MetricsQuery,
  signal?: AbortSignal,
): Promise<InstitutionMetrics> {
  if (!isPeriod(period) || (institutionId !== undefined && !isUuid(institutionId)))
    throw new MetricsApiError("BAD_REQUEST");
  const query = new URLSearchParams({ period });
  if (institutionId) query.set("institution_id", institutionId);
  try {
    const data = parseMetrics(
      await apiFetch(`/api/v1/${role}/metrics/?${query}`, { signal, cache: "no-store" }),
    );
    if (
      data.period !== period ||
      (institutionId
        ? data.scope !== "institution" || data.institution?.id !== institutionId
        : data.scope !== "all")
    )
      invalidResponse();
    return data;
  } catch (error) {
    if (
      error instanceof MetricsApiError ||
      (error instanceof DOMException && error.name === "AbortError")
    )
      throw error;
    const codes: Record<number, MetricsErrorCode> = {
      400: "BAD_REQUEST",
      401: "SESSION_EXPIRED",
      403: "FORBIDDEN",
      404: "NOT_FOUND",
      429: "RATE_LIMIT",
    };
    throw new MetricsApiError(
      error instanceof ApiError
        ? (codes[error.status] ?? (error.status >= 500 ? "SERVER_ERROR" : "UNKNOWN_ERROR"))
        : error instanceof SyntaxError
          ? "INVALID_RESPONSE"
          : error instanceof TypeError
            ? "NETWORK_ERROR"
            : "UNKNOWN_ERROR",
    );
  }
}
export function getOperatorMetrics(
  query: MetricsQuery & { institutionId: string },
  signal?: AbortSignal,
) {
  return request("operator", query, signal);
}
export function getAdminMetrics(query: MetricsQuery, signal?: AbortSignal) {
  return request("admin", query, signal);
}
