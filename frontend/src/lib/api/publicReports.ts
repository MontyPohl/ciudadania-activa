import type {
  PublicCategory,
  PublicReportsPage,
  PublicReportStateFilter,
  PublicReport,
  PublicReportDetailData,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class PublicApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "PublicApiError";
  }
}

interface PublicFetchOptions {
  signal?: AbortSignal;
}

function apiBaseUrl(): URL {
  if (API_URL !== "") return new URL(API_URL);
  if (typeof window !== "undefined") return new URL(window.location.origin);
  throw new PublicApiError("La API pública no tiene un origen configurado.", 500);
}

function resolvePublicUrl(pathOrUrl: string): URL {
  return new URL(pathOrUrl, apiBaseUrl());
}

function errorMessage(status: number): string {
  if (status === 400) return "La consulta del mapa no es válida.";
  if (status === 404) return "No se encontró el reporte solicitado.";
  if (status === 429) return "Se alcanzó el límite de consultas. Intentá de nuevo en un momento.";
  return "No se pudo consultar la información pública.";
}

/** Cliente anónimo: deliberadamente no acepta headers, cookies ni lógica de sesión. */
export async function publicApiFetch<T>(
  pathOrUrl: string,
  { signal }: PublicFetchOptions = {},
): Promise<T> {
  const response = await fetch(resolvePublicUrl(pathOrUrl), {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "omit",
    signal,
  });

  if (!response.ok) throw new PublicApiError(errorMessage(response.status), response.status);
  return (await response.json()) as T;
}

export function getPublicCategories(signal?: AbortSignal): Promise<PublicCategory[]> {
  return publicApiFetch<PublicCategory[]>("/api/v1/public/categories/", { signal });
}

export interface PublicReportsQuery {
  bbox: string;
  stateFilter: PublicReportStateFilter;
  category: string | null;
}

function reportsPath({ bbox, stateFilter, category }: PublicReportsQuery): string {
  const query = new URLSearchParams({ bbox, state_filter: stateFilter });
  if (category !== null) query.set("category", category);
  return `/api/v1/public/reports/?${query.toString()}`;
}

function assertPaginationOrigin(url: URL): void {
  if (url.origin !== apiBaseUrl().origin) {
    throw new PublicApiError("La API devolvió una URL de paginación no permitida.", 502);
  }
}

/** Carga todas las páginas del viewport, con detección de ciclos y cancelación compartida. */
export async function getPublicReports(
  query: PublicReportsQuery,
  signal?: AbortSignal,
): Promise<PublicReportsPage> {
  let next: string | null = reportsPath(query);
  let firstPage: PublicReportsPage | null = null;
  const results: PublicReport[] = [];
  const visited = new Set<string>();

  while (next !== null) {
    signal?.throwIfAborted();
    const url = resolvePublicUrl(next);
    assertPaginationOrigin(url);

    if (visited.has(url.href)) {
      throw new PublicApiError("La API devolvió un ciclo de paginación.", 502);
    }
    visited.add(url.href);

    const page = await publicApiFetch<PublicReportsPage>(url.href, { signal });
    firstPage ??= page;
    results.push(...page.results);
    next = page.next;
  }

  return {
    count: firstPage?.count ?? 0,
    next: null,
    previous: null,
    results,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => keys.includes(key));
}

function isSummary(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["id", "name", "slug"]) &&
    typeof value.id === "string" &&
    UUID_PATTERN.test(value.id) &&
    typeof value.name === "string" &&
    typeof value.slug === "string"
  );
}

function parsePublicReportDetail(value: unknown): PublicReportDetailData {
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
    ]) ||
    typeof value.id !== "string" ||
    !UUID_PATTERN.test(value.id) ||
    !isSummary(value.category) ||
    !(value.institution === null || isSummary(value.institution)) ||
    typeof value.description !== "string" ||
    !["CREADO", "CLASIFICADO", "ASIGNADO", "EN_PROCESO", "RESUELTO", "CERRADO"].includes(
      String(value.status),
    ) ||
    !["BAJA", "MEDIA", "ALTA"].includes(String(value.priority)) ||
    typeof value.latitude !== "number" ||
    !Number.isFinite(value.latitude) ||
    value.latitude < -90 ||
    value.latitude > 90 ||
    typeof value.longitude !== "number" ||
    !Number.isFinite(value.longitude) ||
    value.longitude < -180 ||
    value.longitude > 180 ||
    typeof value.created_at !== "string" ||
    Number.isNaN(Date.parse(value.created_at)) ||
    typeof value.confirmation_count !== "number" ||
    !Number.isInteger(value.confirmation_count) ||
    value.confirmation_count < 0
  ) {
    throw new PublicApiError("La API devolvió un detalle público inválido.", 502);
  }
  return value as unknown as PublicReportDetailData;
}

export async function getPublicReport(
  id: string,
  signal?: AbortSignal,
): Promise<PublicReportDetailData> {
  const response = await publicApiFetch<unknown>(
    `/api/v1/public/reports/${encodeURIComponent(id)}/`,
    { signal },
  );
  return parsePublicReportDetail(response);
}
