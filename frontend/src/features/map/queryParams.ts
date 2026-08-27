import type { PublicReportStateFilter } from "@/types/api";

export interface MapQuery {
  stateFilter: PublicReportStateFilter;
  category: string | null;
  report: string | null;
}

interface SearchParamsLike {
  get(name: string): string | null;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function normalizeStateFilter(value: string | null): PublicReportStateFilter {
  return value === "resolved" || value === "all" ? value : "active";
}

export function normalizeCategory(value: string | null): string | null {
  const trimmed = value?.trim() ?? "";
  return trimmed === "" ? null : trimmed;
}

export function normalizeReportId(value: string | null): string | null {
  return value !== null && UUID_PATTERN.test(value) ? value : null;
}

export function parseMapQuery(searchParams: SearchParamsLike): MapQuery {
  return {
    stateFilter: normalizeStateFilter(searchParams.get("estado")),
    category: normalizeCategory(searchParams.get("categoria")),
    report: normalizeReportId(searchParams.get("reporte")),
  };
}

export function updateMapQuery(
  current: string,
  changes: Partial<{
    estado: PublicReportStateFilter | null;
    categoria: string | null;
    reporte: string | null;
  }>,
): string {
  const source = new URLSearchParams(current);
  const params = new URLSearchParams();
  for (const key of ["estado", "categoria", "reporte"] as const) {
    const value = source.get(key);
    if (value !== null) params.set(key, value);
  }
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === "" || (key === "estado" && value === "active")) {
      params.delete(key);
    } else {
      params.set(key, value);
    }
  }
  const result = params.toString();
  return result === "" ? "" : `?${result}`;
}
