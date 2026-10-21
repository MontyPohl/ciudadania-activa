import { PublicApiError, publicApiFetch } from "@/lib/api/publicReports";
import type { PublicMetrics, PublicSuppressible } from "@/types/api";
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

const suppressible: readonly PublicSuppressible[] = [
  "confirmations",
  "redemptions",
  "credits_issued",
  "resolution_rate",
  "median_resolution_hours",
  "sla.compliance_rate",
];
const cells = {
  received: count,
  validated: count,
  rejected: count,
  resolved: count,
  resolution_rate: nullable(rate),
  median_resolution_hours: nullable(hours),
  confirmations: nullable(count),
  redemptions: nullable(count),
  credits_issued: nullable(count),
  suppressed: (v: unknown) =>
    Array.isArray(v) &&
    new Set(v).size === v.length &&
    v.every((key) => suppressible.includes(key)),
};
function suppressedCells(v: unknown, institutional: boolean): boolean {
  if (
    !object(
      v,
      institutional
        ? {
            ...cells,
            institution: isRef,
            sla: (v) =>
              object(v, {
                configured: boolean,
                hours: nullable(hours),
                compliance_rate: nullable(rate),
              }),
          }
        : cells,
    ) ||
    !isRecord(v) ||
    !Array.isArray(v.suppressed)
  )
    return false;
  return v.suppressed.every((key) =>
    key === "sla.compliance_rate"
      ? institutional && isRecord(v.sla) && v.sla.compliance_rate === null
      : v[key] === null,
  );
}
function newCells(v: unknown, trend: boolean): boolean {
  const keys = trend ? ["received", "resolved"] : ["received"];
  return (
    object(v, {
      ...(trend
        ? { period: (v: unknown) => typeof v === "string" && /^\d{4}-(0[1-9]|1[0-2])$/.test(v) }
        : { category: isRef }),
      ...Object.fromEntries(keys.map((key) => [key, nullable(count)])),
      suppressed: (v: unknown) =>
        Array.isArray(v) && new Set(v).size === v.length && v.every((key) => keys.includes(key)),
    }) &&
    isRecord(v) &&
    Array.isArray(v.suppressed) &&
    keys.every(
      (key) =>
        (v[key] === null) === (v.suppressed as string[]).includes(key) &&
        (v[key] === null || (count(v[key]) && v[key] >= 5)),
    )
  );
}

function chronologicalTrend(v: PublicMetrics): boolean {
  const [year, month] = v.window.from.split("-").map(Number);
  return (
    v.trend.length === 6 &&
    v.trend.every(
      (point, index) =>
        point.period === new Date(Date.UTC(year, month - 1 + index, 1)).toISOString().slice(0, 7),
    )
  );
}

export async function getPublicMetrics(signal?: AbortSignal): Promise<PublicMetrics> {
  try {
    const v: unknown = await publicApiFetch("/api/v1/public/metrics/", { signal });
    if (
      !object(v, {
        window: (v) => object(v, { from: isDate, to: isDate, months: (v) => v === 6 }),
        generated_at: isDateTime,
        min_cell_size: (v) => v === 5,
        totals: (v) => suppressedCells(v, false),
        institutions: list((v) => suppressedCells(v, true)),
        trend: list((v) => newCells(v, true)),
        current_month_by_category: list((v) => newCells(v, false)),
      }) ||
      !chronologicalTrend(v as PublicMetrics)
    )
      throw new PublicApiError("La respuesta de métricas no es válida. Intentá de nuevo.", 502);
    return v as PublicMetrics;
  } catch (error) {
    if (error instanceof SyntaxError)
      throw new PublicApiError("La respuesta de métricas no es válida. Intentá de nuevo.", 502);
    throw error;
  }
}
