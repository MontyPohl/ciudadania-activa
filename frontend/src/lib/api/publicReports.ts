import type {
  PublicCategory,
  PublicReportsPage,
  PublicReportStateFilter,
  PublicReport,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

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

export function getPublicReport(id: string, signal?: AbortSignal): Promise<PublicReport> {
  return publicApiFetch<PublicReport>(`/api/v1/public/reports/${encodeURIComponent(id)}/`, {
    signal,
  });
}
